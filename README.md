# Request Log Management

A small Shiny for Python app for logging delivery requests and viewing intake metrics.

The app is built for Databricks Apps with Unity Catalog storage, while still running locally from a CSV file for quick development.

## Features

- Dashboard tab with total, pending, completed, high-priority, trend, status, priority, and assignee visuals.
- Request form tab with business unit, development type, platform, priority, expected end date, title, and description.
- Manage tab where developers can edit requests, set status to pending/in progress/hold/completed/cancelled, and append comments.
- Automatic assignee selection from configurable business-unit/platform rules.
- Storage switch controlled by environment variables.
- British Gas-inspired visual theme using blue, cyan, and green accents.

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
shiny run --reload --port 8000 app.py
```

Open `http://127.0.0.1:8000`.

With `STORAGE_MODE=csv`, requests are saved to `data/request_logs.csv`.

## Databricks Apps Setup

Set these environment variables in Databricks Apps:

```text
STORAGE_MODE=databricks
UC_CATALOG=main
UC_SCHEMA=request_management
UC_TABLE=request_logs
DATABRICKS_HOST=https://<workspace-hostname>
DATABRICKS_CLUSTER_ID=<cluster-id>
DATABRICKS_TOKEN=<personal-access-token-or-secret>
```

The app uses Databricks Connect to read and write the Unity Catalog Delta table. It checks the configured cluster state with the Databricks SDK. If the cluster is not running, the app triggers startup and shows a cluster status banner until the cluster reaches `RUNNING`.

The app creates the Unity Catalog schema and Delta table if they do not exist. Existing tables are upgraded with `updated_at`, `updated_by`, and `comments` columns when needed.

## Assignment Rules

Edit `ASSIGNMENT_RULES` as JSON:

```json
{
  "Gas|Capacity App": "Gas Capacity Team",
  "Gas|PowerBI": "Gas BI Team",
  "ES|Capacity App": "ES Capacity Team",
  "ES|PowerBI": "ES BI Team"
}
```

Any missing combination goes to `DEFAULT_ASSIGNEE`.

## Main Files

- `app.py` contains the Shiny UI and server logic.
- `request_log/settings.py` reads environment variables.
- `request_log/storage.py` handles CSV and Databricks Unity Catalog storage.
- `request_log/assignments.py` contains the assignment lookup.
- `www/styles.css` contains the app theme.
- `app.yaml` contains Databricks Apps runtime environment settings.
