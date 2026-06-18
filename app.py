from __future__ import annotations

from shiny import App

from request_log.server import create_server
from request_log.settings import get_settings
from request_log.storage import RequestStore
from request_log.ui import create_app_ui


settings = get_settings()
store = RequestStore(settings)

app_ui = create_app_ui(settings)
server = create_server(settings, store)
app = App(app_ui, server)


if __name__ == "__main__":
    import os
    import uvicorn

    port_str = os.environ.get("DATABRICKS_APP_PORT", "8000")
    port = int(port_str) if port_str.strip() else 8000
    uvicorn.run("app:app", host="0.0.0.0", port=port)
