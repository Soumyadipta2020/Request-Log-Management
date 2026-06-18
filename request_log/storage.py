from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import threading
import time

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
STARTING_STATES = {"PENDING", "RESTARTING", "RESIZING", "STARTING"}
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
            self.backend = DatabricksSqlWarehouseStore(settings)
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


class DatabricksSqlWarehouseStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._validate_identifier(settings.uc_catalog)
        self._validate_identifier(settings.uc_schema)
        self._validate_identifier(settings.uc_table)

        self._status_lock = threading.Lock()
        self._cluster_status = {"state": "UNKNOWN", "message": "Checking cluster health...", "can_read": "false"}
        self._bg_thread_started = False
        self._bg_lock = threading.Lock()

    def read_requests(self) -> pd.DataFrame:
        self._ensure_table()
        with self._get_cursor() as cursor:
            cursor.execute(f"SELECT * FROM {self._table_name()} ORDER BY log_date DESC")
            return cursor.fetchall_arrow().to_pandas()

    def add_request(self, request: dict[str, str]) -> None:
        self._ensure_table()
        columns = ", ".join(COLUMNS)
        values = ", ".join(self._sql_literal(request[column]) for column in COLUMNS)
        with self._get_cursor() as cursor:
            cursor.execute(f"INSERT INTO {self._table_name()} ({columns}) VALUES ({values})")

    def update_request(self, request_id: str, updates: dict[str, str], comment: str = "", author: str = "") -> None:
        self._ensure_table()
        with self._get_cursor() as cursor:
            cursor.execute(
                f"SELECT dev_comment FROM {self._table_name()} WHERE request_id = {self._sql_literal(request_id)} LIMIT 1"
            )
            existing = cursor.fetchall_arrow().to_pandas()
            if existing.empty:
                raise ValueError("Selected request could not be found.")

            updates = {column: value for column, value in updates.items() if column in COLUMNS}
            updates["dev_comment"] = append_comment(str(existing.iloc[0]["dev_comment"] or ""), comment, author)
            updates["developer_name"] = author.strip()
            set_clause = ", ".join(f"{column} = {self._sql_literal(value)}" for column, value in updates.items())
            cursor.execute(f"UPDATE {self._table_name()} SET {set_clause} WHERE request_id = {self._sql_literal(request_id)}")

    def cluster_health(self) -> dict[str, str]:
        with self._bg_lock:
            if not self._bg_thread_started:
                self._bg_thread_started = True
                threading.Thread(target=self._status_polling_loop, daemon=True).start()

        with self._status_lock:
            return dict(self._cluster_status)

    def _status_polling_loop(self) -> None:
        while True:
            try:
                state = self._warehouse_state()
                if state == RUNNING_STATE:
                    new_status = {"state": state, "message": "Databricks SQL Warehouse is running.", "can_read": "true"}
                elif state not in STARTING_STATES:
                    try:
                        self._start_warehouse()
                        new_status = {
                            "state": state,
                            "message": f"Warehouse was {state}. Startup has been triggered.",
                            "can_read": "false",
                        }
                    except Exception as exc:
                        new_status = {"state": state, "message": f"Warehouse is {state}. Failed to trigger startup: {exc}", "can_read": "false"}
                else:
                    new_status = {"state": state, "message": "Warehouse is starting. Data will load when it reaches RUNNING.", "can_read": "false"}
            except Exception as exc:
                new_status = {"state": "ERROR", "message": str(exc), "can_read": "false"}

            with self._status_lock:
                self._cluster_status = new_status
                
            time.sleep(15)

    def _get_cursor(self):
        with self._status_lock:
            cached_status = self._cluster_status
        
        state = cached_status.get("state", "UNKNOWN")
        if state != RUNNING_STATE:
            raise ClusterStartingError(state)

        from databricks import sql

        workspace_client = self._workspace_client()
        warehouse = workspace_client.warehouses.get(self.settings.databricks_warehouse_id)
        http_path = warehouse.odbc_params.path

        connection = sql.connect(
            server_hostname=workspace_client.config.host.replace("https://", ""),
            http_path=http_path,
            credentials_provider=lambda: workspace_client.config.authenticate()
        )
        return connection.cursor()

    def _workspace_client(self):
        from databricks.sdk import WorkspaceClient

        kwargs = {}
        if self.settings.databricks_host:
            kwargs["host"] = self.settings.databricks_host
        if self.settings.databricks_token:
            kwargs["token"] = self.settings.databricks_token
        return WorkspaceClient(**kwargs)

    def _warehouse_state(self) -> str:
        if not self.settings.databricks_warehouse_id:
            raise RuntimeError("DATABRICKS_WAREHOUSE_ID is required for Databricks SQL storage.")
        warehouse = self._workspace_client().warehouses.get(self.settings.databricks_warehouse_id)
        return getattr(warehouse.state, "value", str(warehouse.state)).upper()

    def _start_warehouse(self) -> None:
        self._workspace_client().warehouses.start(self.settings.databricks_warehouse_id)

    def _ensure_table(self) -> None:
        with self._get_cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema_name()}")
            cursor.execute(
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
            self._add_missing_columns(cursor)

    def _add_missing_columns(self, cursor) -> None:
        cursor.execute(f"DESCRIBE {self._table_name()}")
        rows = cursor.fetchall_arrow().to_pandas()
        existing_columns = {str(row["col_name"]).lower() for _, row in rows.iterrows()}
        column_types = {
            "requester": "STRING",
            "developer_name": "STRING",
            "dev_comment": "STRING",
        }
        for column, column_type in column_types.items():
            if column not in existing_columns:
                cursor.execute(f"ALTER TABLE {self._table_name()} ADD COLUMNS ({column} {column_type})")

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
