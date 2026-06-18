from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd

from request_log.settings import Settings


COLUMNS = [
    "request_id",
    "requester",
    "buisness_unit",
    "platfor",
    "dev_type",
    "priority",
    "log_date",
    "expected_end_date",
    "title",
    "description",
    "developer_name",
    "status",
    "dev_comment",
]

DATE_COLUMNS = ["log_date", "expected_end_date"]
DEFAULT_STATUSES = ["Pending", "In Progress", "Hold", "Completed", "Cancelled"]
STARTING_STATES = {"PENDING", "RESTARTING", "RESIZING"}
RUNNING_STATE = "RUNNING"


class ClusterStartingError(RuntimeError):
    def __init__(self, state: str):
        self.state = state
        super().__init__(f"Databricks cluster is {state}; startup has been requested.")


def empty_requests() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def new_request(
    request_id: str,
    buisness_unit: str,
    dev_type: str,
    platfor: str,
    priority: str,
    expected_end_date: date,
    title: str,
    description: str,
    developer_name: str,
    requester: str,
) -> dict[str, str]:
    now = now_text()
    return {
        "request_id": request_id,
        "requester": requester.strip(),
        "buisness_unit": buisness_unit,
        "platfor": platfor,
        "dev_type": dev_type,
        "priority": priority,
        "log_date": now,
        "expected_end_date": expected_end_date.isoformat(),
        "title": title.strip(),
        "description": description.strip(),
        "developer_name": developer_name,
        "status": "Pending",
        "dev_comment": "",
    }


def append_comment(existing_comments: str, comment: str, author: str) -> str:
    comment = comment.strip()
    if not comment:
        return existing_comments or ""
    prefix = now_text()
    if author.strip():
        prefix = f"{prefix} - {author.strip()}"
    entry = f"{prefix}: {comment}"
    if existing_comments:
        return f"{existing_comments}\n{entry}"
    return entry


class RequestStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.storage_mode == "databricks":
            self.backend = DatabricksConnectStore(settings)
        else:
            self.backend = CsvStore(settings.local_csv_path)

    def read_requests(self) -> pd.DataFrame:
        return clean_requests(self.backend.read_requests())

    def add_request(self, request: dict[str, str]) -> None:
        self.backend.add_request(request)

    def update_request(self, request_id: str, updates: dict[str, str], comment: str = "", author: str = "") -> None:
        self.backend.update_request(request_id, updates, comment, author)

    def cluster_health(self) -> dict[str, str]:
        if hasattr(self.backend, "cluster_health"):
            return self.backend.cluster_health()
        return {"state": "LOCAL", "message": "Using local CSV storage.", "can_read": "true"}


class CsvStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read_requests(self) -> pd.DataFrame:
        if not self.path.exists():
            return empty_requests()
        return pd.read_csv(self.path, dtype=str).reindex(columns=COLUMNS)

    def add_request(self, request: dict[str, str]) -> None:
        existing = self.read_requests()
        updated = pd.concat([existing, pd.DataFrame([request]).reindex(columns=COLUMNS)], ignore_index=True)
        updated.to_csv(self.path, index=False)

    def update_request(self, request_id: str, updates: dict[str, str], comment: str = "", author: str = "") -> None:
        data = self.read_requests()
        if data.empty:
            raise ValueError("No requests exist yet.")

        mask = data["request_id"] == request_id
        if not mask.any():
            raise ValueError("Selected request could not be found.")

        row_index = data.index[mask][0]
        for column, value in updates.items():
            if column in COLUMNS:
                data.at[row_index, column] = value
        existing_comments = data.at[row_index, "dev_comment"]
        if pd.isna(existing_comments):
            existing_comments = ""
        data.at[row_index, "dev_comment"] = append_comment(str(existing_comments), comment, author)
        data.at[row_index, "developer_name"] = author.strip()
        data.to_csv(self.path, index=False)


class DatabricksConnectStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._spark = None
        self._validate_identifier(settings.uc_catalog)
        self._validate_identifier(settings.uc_schema)
        self._validate_identifier(settings.uc_table)

    def read_requests(self) -> pd.DataFrame:
        spark = self._get_spark()
        self._ensure_table(spark)
        return spark.sql(f"SELECT * FROM {self._table_name()} ORDER BY created_at DESC").toPandas()

    def add_request(self, request: dict[str, str]) -> None:
        spark = self._get_spark()
        self._ensure_table(spark)
        columns = ", ".join(COLUMNS)
        values = ", ".join(self._sql_literal(request[column]) for column in COLUMNS)
        spark.sql(f"INSERT INTO {self._table_name()} ({columns}) VALUES ({values})")

    def update_request(self, request_id: str, updates: dict[str, str], comment: str = "", author: str = "") -> None:
        spark = self._get_spark()
        self._ensure_table(spark)
        existing = spark.sql(
            f"SELECT dev_comment FROM {self._table_name()} WHERE request_id = {self._sql_literal(request_id)} LIMIT 1"
        ).toPandas()
        if existing.empty:
            raise ValueError("Selected request could not be found.")

        updates = {column: value for column, value in updates.items() if column in COLUMNS}
        updates["dev_comment"] = append_comment(str(existing.iloc[0]["dev_comment"] or ""), comment, author)
        updates["developer_name"] = author.strip()
        set_clause = ", ".join(f"{column} = {self._sql_literal(value)}" for column, value in updates.items())
        spark.sql(f"UPDATE {self._table_name()} SET {set_clause} WHERE request_id = {self._sql_literal(request_id)}")

    def cluster_health(self) -> dict[str, str]:
        try:
            state = self._cluster_state()
            if state == RUNNING_STATE:
                return {"state": state, "message": "Databricks cluster is running.", "can_read": "true"}
            if state not in STARTING_STATES:
                self._start_cluster()
                return {
                    "state": state,
                    "message": f"Cluster was {state}. Startup has been triggered.",
                    "can_read": "false",
                }
            return {"state": state, "message": "Cluster is starting. Data will load when it reaches RUNNING.", "can_read": "false"}
        except Exception as exc:
            return {"state": "UNKNOWN", "message": str(exc), "can_read": "false"}

    def _get_spark(self):
        state = self._cluster_state()
        if state != RUNNING_STATE:
            if state not in STARTING_STATES:
                self._start_cluster()
            raise ClusterStartingError(state)

        if self._spark is None:
            from databricks.connect import DatabricksSession
            from databricks.sdk.core import Config

            config_args = {"cluster_id": self.settings.databricks_cluster_id}
            if self.settings.databricks_host:
                config_args["host"] = self.settings.databricks_host
            if self.settings.databricks_token:
                config_args["token"] = self.settings.databricks_token
            config = Config(**config_args)
            self._spark = DatabricksSession.builder.sdkConfig(config).getOrCreate()
        return self._spark

    def _workspace_client(self):
        from databricks.sdk import WorkspaceClient

        kwargs = {}
        if self.settings.databricks_host:
            kwargs["host"] = self.settings.databricks_host
        if self.settings.databricks_token:
            kwargs["token"] = self.settings.databricks_token
        return WorkspaceClient(**kwargs)

    def _cluster_state(self) -> str:
        if not self.settings.databricks_cluster_id:
            raise RuntimeError("DATABRICKS_CLUSTER_ID is required for Databricks Connect storage.")
        cluster = self._workspace_client().clusters.get(self.settings.databricks_cluster_id)
        return getattr(cluster.state, "value", str(cluster.state)).upper()

    def _start_cluster(self) -> None:
        self._workspace_client().clusters.start(self.settings.databricks_cluster_id)

    def _ensure_table(self, spark) -> None:
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {self._schema_name()}")
        spark.sql(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table_name()} (
                request_id STRING,
                requester STRING,
                buisness_unit STRING,
                platfor STRING,
                dev_type STRING,
                priority STRING,
                log_date TIMESTAMP,
                expected_end_date DATE,
                title STRING,
                description STRING,
                developer_name STRING,
                status STRING,
                dev_comment STRING
            )
            USING DELTA
            """
        )
        self._add_missing_columns(spark)

    def _add_missing_columns(self, spark) -> None:
        existing_columns = {column.lower() for column in spark.table(self._table_name()).columns}
        column_types = {
            "requester": "STRING",
            "developer_name": "STRING",
            "dev_comment": "STRING",
        }
        for column, column_type in column_types.items():
            if column not in existing_columns:
                spark.sql(f"ALTER TABLE {self._table_name()} ADD COLUMNS ({column} {column_type})")

    def _schema_name(self) -> str:
        return f"`{self.settings.uc_catalog}`.`{self.settings.uc_schema}`"

    def _table_name(self) -> str:
        return f"{self._schema_name()}.`{self.settings.uc_table}`"

    @staticmethod
    def _validate_identifier(identifier: str) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"Invalid Unity Catalog identifier: {identifier}")

    @staticmethod
    def _sql_literal(value: object) -> str:
        if value is None:
            return "NULL"
        return "'" + str(value).replace("'", "''") + "'"


def clean_requests(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return empty_requests()

    requests = data.reindex(columns=COLUMNS).copy()
    for column in COLUMNS:
        requests[column] = requests[column].fillna("").astype(str)

    for column in DATE_COLUMNS:
        requests[column] = pd.to_datetime(requests[column], errors="coerce")

    requests["status"] = requests["status"].replace("", "Pending")
    requests["priority"] = pd.Categorical(
        requests["priority"],
        categories=["High", "Medium", "Low"],
        ordered=True,
    )
    return requests
