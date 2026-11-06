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
    "developer_email",
    "status",
    "dev_comment",
]

DATE_COLUMNS = ["log_date", "expected_end_date"]
DEFAULT_STATUSES = ["Pending", "In Progress", "Hold", "Completed", "Cancelled"]
STARTING_STATES = {"PENDING", "RESTARTING", "RESIZING", "STARTING"}
STOPPING_STATES = {"TERMINATING", "STOPPING", "DELETING"}
OFFLINE_STATES = {"TERMINATED", "STOPPED", "OFFLINE"}
RUNNING_STATE = "RUNNING"


class ClusterStartingError(RuntimeError):
    def __init__(self, state: str):
        self.state = state
        super().__init__(f"Databricks cluster is {state}; please start the cluster to load data.")


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
    developer_email: str,
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
        "developer_email": developer_email,
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
        return {"state": "LOCAL", "cluster_name": "Local Storage", "message": "Using local CSV storage.", "can_read": "true", "is_offline": "false"}

    def start_cluster(self) -> None:
        if hasattr(self.backend, "start_cluster"):
            self.backend.start_cluster()

    def pause_polling(self) -> None:
        if hasattr(self.backend, "pause_polling"):
            self.backend.pause_polling()

    def resume_polling(self) -> None:
        if hasattr(self.backend, "resume_polling"):
            self.backend.resume_polling()

    def check_health_now(self) -> dict[str, str]:
        if hasattr(self.backend, "check_health_now"):
            return self.backend.check_health_now()
        return self.cluster_health()


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
        data.at[row_index, "developer_email"] = author.strip()
        data.to_csv(self.path, index=False)


class DatabricksSqlWarehouseStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._validate_identifier(settings.uc_catalog)
        self._validate_identifier(settings.uc_schema)
        self._validate_identifier(settings.uc_table)

        self._status_lock = threading.Lock()
        self._compute_name = ""
        self._cluster_status = {
            "state": "UNKNOWN",
            "cluster_name": "",
            "message": "Checking compute status...",
            "can_read": "false",
            "is_offline": "false",
        }
        self._last_status_check = 0.0
        self._startup_in_progress = False
        self._startup_requested_at = 0.0

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
            updates["developer_email"] = author.strip()
            set_clause = ", ".join(f"{column} = {self._sql_literal(value)}" for column, value in updates.items())
            cursor.execute(f"UPDATE {self._table_name()} SET {set_clause} WHERE request_id = {self._sql_literal(request_id)}")

    def cluster_health(self, force_refresh: bool = False) -> dict[str, str]:
        now = time.time()
        with self._status_lock:
            cached = dict(self._cluster_status)
            last_check = getattr(self, "_last_status_check", 0.0)
            state = cached.get("state", "UNKNOWN")

        # Rate limit checks to once every 6 seconds to prevent duplicate queries:
        if not force_refresh and state != "UNKNOWN" and (now - last_check) < 6.0:
            return cached

        return self.check_health_now()

    def pause_polling(self, reason: str = "app is idle") -> None:
        pass

    def resume_polling(self) -> None:
        pass

    def _status_dict(self, name: str, state: str) -> dict[str, str]:
        display_label = name or "Databricks compute"
        if state == RUNNING_STATE:
            return {
                "state": state,
                "cluster_name": name,
                "message": f"'{display_label}' is running.",
                "can_read": "true",
                "is_offline": "false",
            }
        elif state in STARTING_STATES:
            return {
                "state": state,
                "cluster_name": name,
                "message": f"'{display_label}' is {state.lower()}...",
                "can_read": "false",
                "is_offline": "false",
            }
        elif state in STOPPING_STATES:
            return {
                "state": state,
                "cluster_name": name,
                "message": f"'{display_label}' is {state.lower()}...",
                "can_read": "false",
                "is_offline": "false",
            }
        elif state in OFFLINE_STATES:
            return {
                "state": state,
                "cluster_name": name,
                "message": f"'{display_label}' is {state.lower()}.",
                "can_read": "false",
                "is_offline": "true",
            }
        else:
            is_off = state not in {"ERROR", "UNKNOWN"}
            return {
                "state": state,
                "cluster_name": name,
                "message": f"'{display_label}' status: {state}.",
                "can_read": "false",
                "is_offline": "true" if is_off else "false",
            }

    def check_health_now(self) -> dict[str, str]:
        """Performs a direct single on-demand health check."""
        try:
            name, state = self._compute_info()
            if name:
                self._compute_name = name

            elapsed_since_start = time.time() - self._startup_requested_at
            if self._startup_in_progress:
                if state == RUNNING_STATE:
                    self._startup_in_progress = False
                elif state in OFFLINE_STATES and elapsed_since_start < 45:
                    state = "STARTING"
                elif elapsed_since_start >= 300:
                    self._startup_in_progress = False

            new_status = self._status_dict(name, state)
            display_label = name or "Databricks compute"
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [CLUSTER STATUS] '{display_label}' State: {state} | {new_status['message']}", flush=True)
            with self._status_lock:
                self._cluster_status = new_status
                self._last_status_check = time.time()
            return dict(new_status)
        except Exception as exc:
            new_status = {
                "state": "ERROR",
                "cluster_name": self._compute_name,
                "message": f"{type(exc).__name__}: {str(exc)}",
                "can_read": "false",
                "is_offline": "false",
            }
            with self._status_lock:
                self._cluster_status = new_status
                self._last_status_check = time.time()
            return dict(new_status)

    def start_cluster(self) -> None:
        self._startup_in_progress = True
        self._startup_requested_at = time.time()
        target_name = self._compute_name or "compute"
        target_id = self.settings.databricks_cluster_id or self.settings.databricks_warehouse_id or "compute"
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [CLUSTER ACTION] Starting Databricks compute: '{target_name}' ({target_id})...", flush=True)
        self._start_compute()
        with self._status_lock:
            self._cluster_status = {
                "state": "STARTING",
                "cluster_name": self._compute_name,
                "message": f"Startup for '{target_name}' has been initiated. Waiting for cluster to be ready...",
                "can_read": "false",
                "is_offline": "false",
            }
            self._last_status_check = time.time()

    def _get_cursor(self):
        with self._status_lock:
            cached_status = self._cluster_status
        
        state = cached_status.get("state", "UNKNOWN")
        if state != RUNNING_STATE:
            raise ClusterStartingError(state)

        from databricks import sql

        workspace_client = self._workspace_client()

        if self.settings.databricks_cluster_id:
            # Use an All-Purpose Cluster
            cluster_id = self.settings.databricks_cluster_id
            workspace_id = self.settings.databricks_workspace_id
            
            if not workspace_id:
                # Try to extract workspace_id from the databricks host URL
                host = self.settings.databricks_host.replace("https://", "")
                if host.startswith("adb-"):
                    workspace_id = host.split("-")[1].split(".")[0]
                elif host.startswith("dbc-"):
                    workspace_id = host.split("-")[1].split(".")[0]
                else:
                    workspace_id = "0"
                    
            http_path = f"/sql/protocolv1/o/{workspace_id}/{cluster_id}"
        else:
            # Use a Databricks SQL Warehouse
            warehouse = workspace_client.warehouses.get(self.settings.databricks_warehouse_id)
            http_path = warehouse.odbc_params.path

        connect_kwargs = {
            "server_hostname": workspace_client.config.host.replace("https://", ""),
            "http_path": http_path,
        }
        
        if self.settings.databricks_token:
            connect_kwargs["access_token"] = self.settings.databricks_token
        else:
            connect_kwargs["credentials_provider"] = self._sql_credentials_provider(workspace_client.config)

        connection = sql.connect(**connect_kwargs)
        return connection.cursor()

    @staticmethod
    def _sql_credentials_provider(config):
        credentials_provider = getattr(config, "credentials_provider", None)
        if credentials_provider is not None:
            return credentials_provider

        authenticate = getattr(config, "authenticate", None)
        if authenticate is None:
            raise RuntimeError("Databricks SDK config does not expose a SQL credentials provider.")
        return lambda: authenticate

    def _workspace_client(self):
        from databricks.sdk import WorkspaceClient

        kwargs = {}
        if self.settings.databricks_host:
            kwargs["host"] = self.settings.databricks_host
        if self.settings.databricks_token:
            kwargs["token"] = self.settings.databricks_token
        return WorkspaceClient(**kwargs)

    def _compute_info(self) -> tuple[str, str]:
        """Queries Databricks API and returns (cluster_or_warehouse_name, state)."""
        client = self._workspace_client()
        if self.settings.databricks_cluster_id:
            cluster = client.clusters.get(self.settings.databricks_cluster_id)
            state = getattr(cluster.state, "value", str(cluster.state)).upper()
            name = getattr(cluster, "cluster_name", None) or getattr(cluster, "name", "")
            return (str(name) if name else self._compute_name), state

        if not self.settings.databricks_warehouse_id:
            raise RuntimeError("DATABRICKS_WAREHOUSE_ID or DATABRICKS_CLUSTER_ID is required for Databricks SQL storage.")
        warehouse = client.warehouses.get(self.settings.databricks_warehouse_id)
        state = getattr(warehouse.state, "value", str(warehouse.state)).upper()
        name = getattr(warehouse, "name", "")
        return (str(name) if name else self._compute_name), state

    def _warehouse_state(self) -> str:
        _, state = self._compute_info()
        return state

    def _start_compute(self) -> None:
        client = self._workspace_client()
        if self.settings.databricks_cluster_id:
            client.clusters.start(self.settings.databricks_cluster_id)
        else:
            client.warehouses.start(self.settings.databricks_warehouse_id)

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
                    developer_email STRING,
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
            "developer_email": "STRING",
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
