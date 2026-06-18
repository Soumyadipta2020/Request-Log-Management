from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _csv_env(name: str, default: str) -> list[str]:
    raw_value = os.getenv(name, default)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _json_env(name: str, default: str) -> dict[str, str]:
    raw_value = os.getenv(name, default)
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


@dataclass(frozen=True)
class Settings:
    storage_mode: str
    local_csv_path: Path
    uc_catalog: str
    uc_schema: str
    uc_table: str
    databricks_host: str
    databricks_cluster_id: str
    databricks_token: str
    default_assignee: str
    business_units: list[str]
    development_types: list[str]
    platforms: list[str]
    priorities: list[str]
    request_statuses: list[str]
    assignment_rules: dict[str, str]

    @property
    def uc_full_table_name(self) -> str:
        return f"{self.uc_catalog}.{self.uc_schema}.{self.uc_table}"


def get_settings() -> Settings:
    return Settings(
        storage_mode=os.getenv("STORAGE_MODE", "csv").strip().lower(),
        local_csv_path=Path(os.getenv("LOCAL_CSV_PATH", "data/table.csv")),
        uc_catalog=os.getenv("UC_CATALOG", "main").strip(),
        uc_schema=os.getenv("UC_SCHEMA", os.getenv("UC_DATABASE", "request_management")).strip(),
        uc_table=os.getenv("UC_TABLE", "request_logs").strip(),
        databricks_host=os.getenv("DATABRICKS_HOST", "").strip(),
        databricks_cluster_id=os.getenv("DATABRICKS_CLUSTER_ID", "").strip(),
        databricks_token=os.getenv("DATABRICKS_TOKEN", "").strip(),
        default_assignee=os.getenv("DEFAULT_ASSIGNEE", "Shared Delivery Queue").strip(),
        business_units=_csv_env("BUSINESS_UNITS", "Gas,ES"),
        development_types=_csv_env("DEVELOPMENT_TYPES", "Fault,Dev"),
        platforms=_csv_env("PLATFORMS", "Capacity App,PowerBI"),
        priorities=_csv_env("PRIORITIES", "High,Medium,Low"),
        request_statuses=_csv_env("REQUEST_STATUSES", "Pending,In Progress,Hold,Completed,Cancelled"),
        assignment_rules=_json_env(
            "ASSIGNMENT_RULES",
            '{"Gas|Capacity App":"Gas Capacity Team","Gas|PowerBI":"Gas BI Team","ES|Capacity App":"ES Capacity Team","ES|PowerBI":"ES BI Team"}',
        ),
    )
