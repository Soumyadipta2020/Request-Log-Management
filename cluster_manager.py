from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from shiny import ui

if TYPE_CHECKING:
    from request_log.settings import Settings

# Databricks Compute States
STARTING_STATES = {"PENDING", "RESTARTING", "RESIZING", "STARTING"}
STOPPING_STATES = {"TERMINATING", "STOPPING", "DELETING"}
OFFLINE_STATES = {"TERMINATED", "STOPPED", "OFFLINE"}
RUNNING_STATE = "RUNNING"
UNKNOWN_STATE = "UNKNOWN"
ERROR_STATE = "ERROR"


class ClusterStartingError(RuntimeError):
    """Raised when an attempt to execute a query is made while compute is starting or offline."""

    def __init__(self, state: str):
        self.state = state
        super().__init__(f"Databricks cluster is {state}; please start the cluster to load data.")


def is_offline_state(state: str) -> bool:
    """Returns True if the state indicates the compute is offline/stopped/terminated."""
    return state.upper() in OFFLINE_STATES


def is_starting_state(state: str) -> bool:
    """Returns True if the compute is actively starting up or resizing."""
    return state.upper() in STARTING_STATES


def is_running_state(state: str) -> bool:
    """Returns True if the compute is running and ready to accept queries."""
    return state.upper() == RUNNING_STATE


def format_cluster_status_dict(name: str, state: str) -> dict[str, str]:
    """Generates standard status dictionary for the given cluster name and state."""
    display_label = name or "Databricks compute"
    state_upper = state.upper() if state else UNKNOWN_STATE

    if state_upper == RUNNING_STATE:
        return {
            "state": state_upper,
            "cluster_name": name,
            "message": f"'{display_label}' is running.",
            "can_read": "true",
            "is_offline": "false",
        }
    elif state_upper in STARTING_STATES:
        return {
            "state": state_upper,
            "cluster_name": name,
            "message": f"'{display_label}' is {state_upper.lower()}...",
            "can_read": "false",
            "is_offline": "false",
        }
    elif state_upper in STOPPING_STATES:
        return {
            "state": state_upper,
            "cluster_name": name,
            "message": f"'{display_label}' is {state_upper.lower()}...",
            "can_read": "false",
            "is_offline": "false",
        }
    elif state_upper in OFFLINE_STATES:
        return {
            "state": state_upper,
            "cluster_name": name,
            "message": f"'{display_label}' is {state_upper.lower()}.",
            "can_read": "false",
            "is_offline": "true",
        }
    else:
        is_off = state_upper not in {ERROR_STATE, UNKNOWN_STATE}
        return {
            "state": state_upper,
            "cluster_name": name,
            "message": f"'{display_label}' status: {state_upper}.",
            "can_read": "false",
            "is_offline": "true" if is_off else "false",
        }


class ClusterManager:
    """
    Manages Databricks compute (SQL Warehouse or All-Purpose Cluster) lifecycle,
    cached health polling, startup requests, and SQL database connection cursors.
    """

    def __init__(self, settings: Any):
        self.settings = settings
        self._status_lock = threading.Lock()
        self._compute_name = ""
        self._cluster_status = {
            "state": UNKNOWN_STATE,
            "cluster_name": "",
            "message": "Checking compute status...",
            "can_read": "false",
            "is_offline": "false",
        }
        self._last_status_check = 0.0
        self._startup_in_progress = False
        self._startup_requested_at = 0.0

    @property
    def compute_name(self) -> str:
        with self._status_lock:
            return self._compute_name or self._cluster_status.get("cluster_name", "")

    @property
    def current_status(self) -> dict[str, str]:
        with self._status_lock:
            return dict(self._cluster_status)

    @property
    def is_running(self) -> bool:
        with self._status_lock:
            return self._cluster_status.get("state") == RUNNING_STATE

    @property
    def is_offline(self) -> bool:
        with self._status_lock:
            state = self._cluster_status.get("state", UNKNOWN_STATE)
            return self._cluster_status.get("is_offline") == "true" or state in OFFLINE_STATES

    @property
    def is_starting(self) -> bool:
        with self._status_lock:
            state = self._cluster_status.get("state", UNKNOWN_STATE)
            return state in STARTING_STATES or self._startup_in_progress

    def get_workspace_client(self):
        """Constructs and returns an authenticated Databricks WorkspaceClient."""
        from databricks.sdk import WorkspaceClient

        kwargs = {}
        if getattr(self.settings, "databricks_host", None):
            kwargs["host"] = self.settings.databricks_host
        if getattr(self.settings, "databricks_token", None):
            kwargs["token"] = self.settings.databricks_token
        return WorkspaceClient(**kwargs)

    def get_compute_info(self) -> tuple[str, str]:
        """Queries the Databricks API and returns (cluster_or_warehouse_name, state)."""
        client = self.get_workspace_client()

        cluster_id = getattr(self.settings, "databricks_cluster_id", None)
        if cluster_id:
            cluster = client.clusters.get(cluster_id)
            state = getattr(cluster.state, "value", str(cluster.state)).upper()
            name = getattr(cluster, "cluster_name", None) or getattr(cluster, "name", "")
            return (str(name) if name else self._compute_name), state

        warehouse_id = getattr(self.settings, "databricks_warehouse_id", None)
        if not warehouse_id:
            raise RuntimeError("DATABRICKS_WAREHOUSE_ID or DATABRICKS_CLUSTER_ID is required for Databricks SQL storage.")

        warehouse = client.warehouses.get(warehouse_id)
        state = getattr(warehouse.state, "value", str(warehouse.state)).upper()
        name = getattr(warehouse, "name", "")
        return (str(name) if name else self._compute_name), state

    def check_health_now(self) -> dict[str, str]:
        """Performs a direct single on-demand health check against Databricks API."""
        try:
            name, state = self.get_compute_info()
            if name:
                self._compute_name = name

            elapsed_since_start = time.time() - self._startup_requested_at
            if self._startup_in_progress:
                if state == RUNNING_STATE:
                    self._startup_in_progress = False
                elif state in OFFLINE_STATES and elapsed_since_start < 45:
                    # Databricks takes 5-20s after API call to update state from STOPPED to STARTING.
                    # Preserve STARTING state during this transition window.
                    state = "STARTING"
                elif elapsed_since_start >= 300:
                    self._startup_in_progress = False

            new_status = format_cluster_status_dict(name, state)
            display_label = name or "Databricks compute"
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [CLUSTER STATUS] '{display_label}' State: {state} | {new_status['message']}", flush=True)
            with self._status_lock:
                self._cluster_status = new_status
                self._last_status_check = time.time()
            return dict(new_status)
        except Exception as exc:
            new_status = {
                "state": ERROR_STATE,
                "cluster_name": self._compute_name,
                "message": f"{type(exc).__name__}: {str(exc)}",
                "can_read": "false",
                "is_offline": "false",
            }
            with self._status_lock:
                self._cluster_status = new_status
                self._last_status_check = time.time()
            return dict(new_status)

    def cluster_health(self, force_refresh: bool = False) -> dict[str, str]:
        """
        Returns cached status if queried within 6 seconds; otherwise queries Databricks.
        This rate-limits polling checks and prevents spamming the Databricks API.
        """
        now = time.time()
        with self._status_lock:
            cached = dict(self._cluster_status)
            last_check = self._last_status_check
            state = cached.get("state", UNKNOWN_STATE)

        if not force_refresh and state != UNKNOWN_STATE and (now - last_check) < 6.0:
            return cached

        return self.check_health_now()

    def start_cluster(self) -> None:
        """Sends startup request to Databricks for the configured warehouse or cluster."""
        self._startup_in_progress = True
        self._startup_requested_at = time.time()
        target_name = self._compute_name or "compute"
        target_id = getattr(self.settings, "databricks_cluster_id", None) or getattr(self.settings, "databricks_warehouse_id", None) or "compute"
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

    def _start_compute(self) -> None:
        client = self.get_workspace_client()
        if getattr(self.settings, "databricks_cluster_id", None):
            client.clusters.start(self.settings.databricks_cluster_id)
        else:
            client.warehouses.start(self.settings.databricks_warehouse_id)

    def pause_polling(self, reason: str = "app is idle") -> None:
        """Compatibility no-op for polling pause."""
        pass

    def resume_polling(self) -> None:
        """Compatibility no-op for polling resume."""
        pass

    def get_cursor(self):
        """
        Verifies compute is RUNNING and opens a database cursor via Databricks SQL connector.
        Raises ClusterStartingError if compute is not currently in RUNNING state.
        """
        with self._status_lock:
            cached_status = self._cluster_status

        state = cached_status.get("state", UNKNOWN_STATE)
        if state != RUNNING_STATE:
            raise ClusterStartingError(state)

        from databricks import sql

        workspace_client = self.get_workspace_client()

        cluster_id = getattr(self.settings, "databricks_cluster_id", None)
        if cluster_id:
            workspace_id = getattr(self.settings, "databricks_workspace_id", None)
            if not workspace_id:
                host = self.settings.databricks_host.replace("https://", "")
                if host.startswith("adb-") or host.startswith("dbc-"):
                    workspace_id = host.split("-")[1].split(".")[0]
                else:
                    workspace_id = "0"
            http_path = f"/sql/protocolv1/o/{workspace_id}/{cluster_id}"
        else:
            warehouse = workspace_client.warehouses.get(self.settings.databricks_warehouse_id)
            http_path = warehouse.odbc_params.path

        connect_kwargs = {
            "server_hostname": workspace_client.config.host.replace("https://", ""),
            "http_path": http_path,
        }

        if getattr(self.settings, "databricks_token", None):
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


# ---------------------------------------------------------------------------
# Cluster UI Components & Modals
# ---------------------------------------------------------------------------

def cluster_offline_modal(cluster_name: str = "", state: str = "OFFLINE") -> ui.Tag:
    """Displays the floating modal prompting the user to start the stopped/offline compute."""
    display_state = state if state else "OFFLINE"
    has_name = bool(cluster_name and cluster_name.strip())

    if has_name:
        subtitle_text = f"The compute cluster '{cluster_name}' is currently {display_state.lower()}. Would you like to start the cluster to load data and use the app?"
    else:
        subtitle_text = f"The compute cluster is currently {display_state.lower()}. Would you like to start the cluster to load data and use the app?"

    badge_items = []
    if has_name:
        badge_items.extend([
            ui.div("CLUSTER NAME", class_="status-modal-id-label"),
            ui.div(cluster_name, class_="status-modal-id-value", style="color: #1a202c; font-size: 1.15rem; font-weight: 600; margin-bottom: 10px; word-break: break-word;"),
        ])
    badge_items.extend([
        ui.div("COMPUTE STATUS", class_="status-modal-id-label"),
        ui.div(display_state.upper(), class_="status-modal-id-value", style="color: #e07000; font-size: 1.25rem; font-weight: 700;"),
    ])

    return ui.modal(
        ui.div(
            ui.div(
                ui.div(
                    ui.HTML('''
                        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
                            <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
                            <line x1="6" y1="6" x2="6.01" y2="6"></line>
                            <line x1="6" y1="18" x2="6.01" y2="18"></line>
                        </svg>
                    '''),
                    class_="status-modal-icon-circle cluster-offline",
                ),
                ui.div("Databricks Cluster is Offline", class_="status-modal-title"),
                ui.div(subtitle_text, class_="status-modal-subtitle"),
                ui.div(
                    ui.div(
                        *badge_items,
                        class_="status-modal-badge-card",
                        style="padding: 14px 20px; margin-bottom: 20px; text-align: center;",
                    ),
                    style="width: 100%; display: flex; justify-content: center;",
                ),
                ui.div(
                    ui.input_action_button(
                        "btn_start_cluster_confirm",
                        "Yes, Start Cluster",
                        class_="btn-primary",
                        style="min-width: 160px; font-weight: 700; padding: 10px 20px;",
                    ),
                    ui.input_action_button(
                        "btn_offline_modal_dismiss",
                        "Cancel",
                        class_="btn-outline-secondary",
                        style="min-width: 100px; padding: 10px 20px;",
                    ),
                    style="display: flex; justify-content: center; gap: 12px; margin-top: 6px;",
                ),
                class_="status-modal-content",
            ),
            class_="status-modal-wrapper",
        ),
        title=None,
        footer=None,
        easy_close=False,
        size="m",
    )


def cluster_starting_modal(cluster_name: str = "") -> ui.Tag:
    """Displays the floating modal with spinner while compute startup is in progress."""
    target_text = f" '{cluster_name}'" if cluster_name and cluster_name.strip() else ""
    return ui.modal(
        ui.div(
            ui.div(
                ui.div(class_="status-modal-spinner"),
                ui.div(f"Starting Databricks Cluster{target_text}...", class_="status-modal-title"),
                ui.div(
                    "Startup has been requested in Databricks. It usually takes 2 to 4 minutes for compute to become ready. The app will automatically connect and load data once online.",
                    class_="status-modal-subtitle",
                ),
                ui.div(
                    ui.input_action_button(
                        "btn_dismiss_starting_modal",
                        "Wait in Background",
                        class_="btn-outline-secondary compact-btn",
                        style="padding: 8px 18px;",
                    ),
                    style="display: flex; justify-content: center; margin-top: 10px;",
                ),
                class_="status-modal-content",
            ),
            class_="status-modal-wrapper",
        ),
        title=None,
        footer=None,
        easy_close=False,
        size="m",
    )


def cluster_error_modal(error_message: str) -> ui.Tag:
    """Displays error modal if cluster startup encountered an exception."""
    return ui.modal(
        ui.div(
            ui.div(
                ui.div(
                    ui.HTML('''
                        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    '''),
                    class_="status-modal-icon-circle error",
                ),
                ui.div("Cluster Startup Failed", class_="status-modal-title"),
                ui.div(
                    f"An error occurred while attempting to start the cluster: {error_message}",
                    class_="status-modal-subtitle",
                ),
                ui.div(
                    ui.input_action_button(
                        "btn_offline_modal_dismiss",
                        "Close",
                        class_="btn-outline-secondary compact-btn",
                        style="padding: 8px 20px;",
                    ),
                    style="display: flex; justify-content: center; margin-top: 10px;",
                ),
                class_="status-modal-content",
            ),
            class_="status-modal-wrapper",
        ),
        title=None,
        footer=None,
        easy_close=True,
        size="m",
    )


def render_cluster_banner(cluster_info: dict[str, str], storage_mode: str = "databricks") -> ui.Tag | None:
    """Renders the top status banner when compute is stopped, starting, or in an error state."""
    state = cluster_info.get("state", "LOCAL")
    cluster_name = cluster_info.get("cluster_name", "")
    message = cluster_info.get("message", "")

    if storage_mode != "databricks" or state == RUNNING_STATE:
        return None

    banner_class = "ok" if cluster_info.get("can_read") == "true" else "warn"
    is_offline = cluster_info.get("is_offline") == "true" or state in OFFLINE_STATES
    is_starting = state in STARTING_STATES

    action_btn = None
    if is_offline:
        action_btn = ui.input_action_button(
            "btn_banner_start_cluster",
            "Start Cluster",
            class_="btn-primary compact-btn",
            style="margin-left: auto;",
        )
    elif is_starting:
        action_btn = ui.div(
            ui.tags.span(
                class_="spinner-border spinner-border-sm text-primary",
                role="status",
                style="width: 0.9rem; height: 0.9rem; margin-right: 6px;",
            ),
            ui.tags.span("Starting..."),
            style="margin-left: auto; display: flex; align-items: center; font-size: 0.85rem; font-weight: 700; color: var(--bg-blue);",
        )

    display_state_label = f"{cluster_name}: {state}" if cluster_name else f"Cluster {state}"

    return ui.div(
        ui.tags.span(display_state_label, class_="health-state"),
        ui.tags.span(message),
        action_btn,
        class_=f"health-banner {banner_class}",
    )


if __name__ == "__main__":
    from request_log.settings import get_settings

    print("Testing Databricks Compute ClusterManager...")
    cfg = get_settings()
    manager = ClusterManager(cfg)
    try:
        health = manager.check_health_now()
        print("Compute Health Status:")
        for k, v in health.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"Error checking health: {e}")
