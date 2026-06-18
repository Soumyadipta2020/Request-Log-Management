from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shiny import App, Inputs, Outputs, Session, reactive, render, ui
from shinywidgets import output_widget, render_widget

from request_log.assignments import assign_owner
from request_log.settings import get_settings
from request_log.storage import ClusterStartingError, RequestStore, empty_requests, new_request


settings = get_settings()
store = RequestStore(settings)

BG_COLORS = ["#043b7a", "#00aeef", "#7ac143", "#f3b61f", "#ff8a8a", "#667085"]
STATUS_COLORS = {
    "Pending": "#00aeef",
    "In Progress": "#043b7a",
    "Hold": "#f3b61f",
    "Completed": "#7ac143",
    "Cancelled": "#ff8a8a",
}
PRIORITY_COLORS = {
    "High": "#043b7a",
    "Medium": "#f3b61f",
    "Low": "#7ac143",
}
ALL_FILTER = "All"
DEFAULT_FILTER_START = date(2020, 1, 1)
DEFAULT_FILTER_END = date.today() + timedelta(days=730)


def metric_card(label: str, value: str, note: str, tone: str = "blue") -> ui.Tag:
    return ui.div(
        ui.div(ui.tags.span(class_=f"metric-icon {tone}"), ui.tags.span(label), class_="metric-label"),
        ui.div(value, class_="metric-value"),
        ui.div(note, class_="metric-note"),
        class_=f"metric-card {tone}",
    )


def section_header(title: str, subtitle: str = "") -> ui.Tag:
    return ui.div(
        ui.div(title, class_="panel-title"),
        ui.div(subtitle, class_="panel-subtitle") if subtitle else None,
        class_="section-header",
    )


def sidebar_link(input_id: str, label: str, active: bool = False) -> ui.Tag:
    state = " active" if active else ""
    return ui.input_action_link(input_id, label, class_=f"side-item{state}")


def plot_template(title: str):
    return {
        "layout": {
            "title": {"text": title, "font": {"size": 15, "color": "#043b7a"}},
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "font": {"family": "Inter, Segoe UI, Arial", "color": "#172033", "size": 12},
            "margin": {"l": 18, "r": 18, "t": 42, "b": 28},
        }
    }


def empty_chart(title: str, message: str = "No requests match these filters") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(plot_template(title)["layout"])
    fig.update_layout(
        xaxis={"visible": False},
        yaxis={"visible": False},
        annotations=[
            {
                "text": message,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "showarrow": False,
                "font": {"size": 14, "color": "#657386"},
            }
        ],
    )
    return fig


app_ui = ui.page_fluid(
    ui.include_css("www/styles.css"),
    ui.div(
        ui.tags.aside(
            ui.div("BRITISH GAS", class_="logo"),
            ui.div("REQUESTS", class_="side-heading"),
            ui.output_ui("sidebar_nav"),
            ui.div("CONFIG", class_="side-heading"),
            ui.div(f"Storage: {settings.storage_mode.upper()}", class_="side-note"),
            ui.div(f"Table: {settings.uc_full_table_name}", class_="side-note"),
            class_="app-sidebar",
        ),
        ui.tags.main(
            ui.div(
                ui.div(
                    ui.h1("Request Journey Overview", class_="page-title"),
                    ui.p("British Gas delivery request intake, ownership, and status tracking.", class_="page-subtitle"),
                ),
                ui.div(
                    ui.input_action_button("refresh", "Refresh", class_="btn-outline-secondary compact-btn"),
                    class_="header-actions",
                ),
                class_="topbar",
            ),
            ui.output_ui("cluster_banner"),
            ui.navset_hidden(
                ui.nav_panel(
                    "Visuals",
                    ui.div(
                        ui.div(
                            section_header("Dashboard Filters", "Visuals and totals update together"),
                            ui.div(
                                ui.input_select("filter_business_unit", "Business unit", [ALL_FILTER, *settings.business_units]),
                                ui.input_select("filter_priority", "Priority", [ALL_FILTER, *settings.priorities]),
                                ui.input_select("filter_status", "Status", [ALL_FILTER, *settings.request_statuses]),
                                ui.input_select("filter_platform", "Platform", [ALL_FILTER, *settings.platforms]),
                                ui.input_select("filter_development_type", "Development type", [ALL_FILTER, *settings.development_types]),
                                ui.input_date_range(
                                    "filter_created",
                                    "Created date",
                                    start=DEFAULT_FILTER_START,
                                    end=DEFAULT_FILTER_END,
                                ),
                                class_="filter-grid",
                            ),
                            ui.div(
                                ui.input_action_button("clear_filters", "Clear filters", class_="btn-outline-secondary compact-btn"),
                                ui.output_text("filter_context"),
                                class_="filter-actions",
                            ),
                            class_="content-panel filter-panel",
                        ),
                        ui.output_ui("summary_metrics"),
                        ui.div(
                            ui.div(
                                section_header("Monthly Requests", "Stacked by priority category"),
                                output_widget("monthly_priority_chart"),
                                class_="content-panel chart-wide",
                            ),
                            ui.div(
                                section_header("Status Mix"),
                                output_widget("status_chart"),
                                class_="content-panel",
                            ),
                            ui.div(
                                section_header("Priority Mix"),
                                output_widget("priority_chart"),
                                class_="content-panel",
                            ),
                            ui.div(
                                section_header("Assigned Work"),
                                output_widget("assignee_chart"),
                                class_="content-panel",
                            ),
                            class_="dashboard-grid",
                        ),
                        ui.div(
                            section_header("Recent Requests", "Latest 25 requests in the current filter"),
                            ui.output_data_frame("recent_requests"),
                            class_="content-panel table-panel",
                        ),
                        class_="tab-content-shell",
                    ),
                ),
                ui.nav_panel(
                    "Log Request",
                    ui.div(
                        ui.div(
                                section_header("Request Details"),
                                ui.div(
                                    ui.input_select("business_unit", "Business unit", settings.business_units),
                                    ui.input_select("development_type", "Development type", settings.development_types),
                                    ui.input_select("platform", "Platform", settings.platforms),
                                    ui.input_select("priority", "Priority", settings.priorities),
                                    ui.input_date(
                                        "expected_end_date",
                                        "Expected end date",
                                        value=date.today() + timedelta(days=14),
                                        min=date.today(),
                                    ),
                                    ui.input_text("email", "Your email", placeholder="name@example.com"),
                                    ui.div(
                                        ui.input_text("title", "Title", placeholder="Short request title"),
                                        class_="span-3",
                                    ),
                                    ui.div(
                                        ui.input_text_area(
                                            "description",
                                            "Description",
                                            placeholder="What needs to change, why it matters, and any useful context",
                                            width="100%",
                                            height="130px",
                                        ),
                                        class_="span-3",
                                    ),
                                    class_="input-grid",
                                ),
                                ui.div(
                                    ui.input_action_button("submit_request", "Submit request", class_="btn-primary"),
                                    ui.input_action_button("clear_form", "Clear", class_="btn-outline-secondary"),
                                    class_="button-row",
                                ),
                                class_="content-panel form-panel",
                            ),
                        class_="tab-content-shell",
                    ),
                ),
                ui.nav_panel(
                    "Manage",
                    ui.div(
                        ui.div(
                            ui.div(
                                section_header("Select Request", "Pick an item to update or view all details"),
                                ui.input_select("manage_request_id", "Request", choices={"": "No requests loaded"}),
                                class_="content-panel",
                                style="margin-bottom: 14px;",
                            ),
                            ui.div(
                                ui.output_ui("manage_details_view"),
                                ui.hr(style="margin: 24px 0; border-color: var(--line);"),
                                section_header("Developer Update", "Modify status, severity, end date, or add comments"),
                                ui.div(
                                    ui.input_text("developer_name", "Developer name", placeholder="Your name"),
                                    ui.input_select("manage_status", "Status", settings.request_statuses),
                                    ui.input_select("manage_priority", "Severity (Priority)", settings.priorities),
                                    ui.input_date("manage_expected_end_date", "Expected end date", value=date.today()),
                                    ui.div(
                                        ui.input_text_area(
                                            "manage_comment",
                                            "Add comment",
                                            placeholder="Add progress notes, blocker detail, or closure notes",
                                            height="96px",
                                            width="100%",
                                        ),
                                        class_="span-2",
                                    ),
                                    class_="input-grid compact",
                                ),
                                ui.div(
                                    ui.input_action_button("save_update", "Save update", class_="btn-primary"),
                                    class_="button-row",
                                ),
                                ui.hr(style="margin: 24px 0; border-color: var(--line);"),
                                section_header("Comment History"),
                                ui.output_ui("comment_history"),
                                class_="content-panel",
                            ),
                        ),
                        class_="tab-content-shell",
                    ),
                ),
                id="main_tabs",
                selected="Visuals",
            ),
            class_="main-workspace",
        ),
        class_="app-shell",
    ),
    title="British Gas Request Management",
)


def server(input: Inputs, output: Outputs, session: Session):
    refresh_count = reactive.value(0)
    data_error = reactive.value("")
    cluster_info = reactive.value(store.cluster_health())

    @reactive.effect
    @reactive.event(input.refresh)
    def refresh_data():
        refresh_count.set(refresh_count() + 1)

    @reactive.calc
    def requests() -> pd.DataFrame:
        refresh_count()
        if settings.storage_mode == "databricks":
            reactive.invalidate_later(15, session=session)
        try:
            health = store.cluster_health()
            cluster_info.set(health)
            if health.get("can_read") != "true":
                data_error.set(health.get("message", "Databricks cluster is not ready."))
                return empty_requests()
            data = store.read_requests()
            data_error.set("")
            return data
        except ClusterStartingError as exc:
            data_error.set(str(exc))
            cluster_info.set({"state": exc.state, "message": str(exc), "can_read": "false"})
            return empty_requests()
        except Exception as exc:
            data_error.set(str(exc))
            cluster_info.set({"state": "ERROR", "message": str(exc), "can_read": "false"})
            return empty_requests()

    @reactive.calc
    def filtered_requests() -> pd.DataFrame:
        data = requests()
        if data.empty:
            return data

        filtered = data.copy()
        filters = {
            "buisness_unit": input.filter_business_unit(),
            "priority": input.filter_priority(),
            "status": input.filter_status(),
            "platfor": input.filter_platform(),
            "dev_type": input.filter_development_type(),
        }
        for column, selected in filters.items():
            if selected and selected != ALL_FILTER:
                filtered = filtered[filtered[column].astype(str) == selected]

        date_range = input.filter_created()
        if date_range:
            start, end = date_range
            if start:
                filtered = filtered[filtered["log_date"].dt.date >= date_value(start)]
            if end:
                filtered = filtered[filtered["log_date"].dt.date <= date_value(end)]
        return filtered

    @reactive.calc
    def request_choices() -> dict[str, str]:
        data = requests()
        if data.empty:
            return {"": "No requests loaded"}
        sorted_data = data.sort_values("log_date", ascending=False)
        return {
            row.request_id: f"[{row.request_id}] {row.title[:70]} | {row.status} | {row.developer_name}"
            for row in sorted_data.itertuples(index=False)
        }

    @reactive.calc
    def selected_request() -> pd.Series | None:
        request_id = input.manage_request_id()
        data = requests()
        if not request_id or data.empty:
            return None
        selected = data[data["request_id"] == request_id]
        if selected.empty:
            return None
        return selected.iloc[0]

    @reactive.calc
    def selected_assignee() -> str:
        return assign_owner(settings, input.business_unit(), input.platform())

    @render.ui
    def sidebar_nav():
        active_tab = input.main_tabs() or "Visuals"
        return ui.div(
            sidebar_link("nav_visuals", "Visuals", active_tab == "Visuals"),
            sidebar_link("nav_log_request", "Log Request", active_tab == "Log Request"),
            sidebar_link("nav_manage", "Manage", active_tab == "Manage"),
            class_="side-nav",
        )

    @reactive.effect
    @reactive.event(input.nav_visuals)
    def show_visuals():
        ui.update_navs("main_tabs", selected="Visuals")

    @reactive.effect
    @reactive.event(input.nav_log_request)
    def show_log_request():
        ui.update_navs("main_tabs", selected="Log Request")

    @reactive.effect
    @reactive.event(input.nav_manage)
    def show_manage():
        ui.update_navs("main_tabs", selected="Manage")

    @render.ui
    def cluster_banner():
        info = cluster_info()
        state = info.get("state", "LOCAL")
        message = info.get("message", "")
        if settings.storage_mode != "databricks":
            return ui.div("Local CSV mode is active. Switch STORAGE_MODE to databricks for Unity Catalog Delta storage.", class_="health-banner ok")
        banner_class = "ok" if info.get("can_read") == "true" else "warn"
        return ui.div(
            ui.tags.span(f"Cluster {state}", class_="health-state"),
            ui.tags.span(message),
            class_=f"health-banner {banner_class}",
        )

    @render.ui
    def assignee_preview():
        return ui.div(
            ui.div("Assigned to", class_="metric-label"),
            ui.div(selected_assignee(), class_="metric-value small"),
            ui.div("Calculated from business unit and platform.", class_="metric-note"),
            class_="assignment-card",
        )

    @render.text
    def filter_context():
        total = len(requests())
        filtered_total = len(filtered_requests())
        if filtered_total == total:
            return f"Showing all {total} requests"
        return f"Showing {filtered_total} of {total} requests"

    @render.ui
    def summary_metrics():
        data = filtered_requests()
        total = len(data)
        status = data["status"].astype(str).str.lower() if total else pd.Series(dtype=str)
        pending = int((status == "pending").sum()) if total else 0
        in_progress = int((status == "in progress").sum()) if total else 0
        hold = int((status == "hold").sum()) if total else 0
        completed = int((status == "completed").sum()) if total else 0
        cancelled = int((status == "cancelled").sum()) if total else 0
        return ui.div(
            metric_card("Total Requests", str(total), "All logged demand", "blue"),
            metric_card("Pending", str(pending), "Awaiting action", "cyan"),
            metric_card("In Progress", str(in_progress), "Being delivered", "navy"),
            metric_card("Hold", str(hold), "Paused or blocked", "amber"),
            metric_card("Completed", str(completed), "Closed successfully", "green"),
            metric_card("Cancelled", str(cancelled), "Stopped requests", "red"),
            class_="metric-grid",
        )

    @render_widget
    def status_chart():
        data = filtered_requests()
        if data.empty:
            fig = empty_chart("Requests by status")
            fig.update_layout(height=270)
            return fig

        counts = data["status"].astype(str).value_counts()
        chart_data = pd.DataFrame(
            {
                "Status": settings.request_statuses,
                "Requests": [int(counts.get(status, 0)) for status in settings.request_statuses],
            }
        )
        chart_data = chart_data[chart_data["Requests"] > 0]
        fig = px.pie(
            chart_data,
            names="Status",
            values="Requests",
            hole=0.52,
            color="Status",
            color_discrete_map=STATUS_COLORS,
            color_discrete_sequence=BG_COLORS,
        )
        fig.update_traces(textinfo="label+percent+value", hovertemplate="%{label}: %{value} requests<extra></extra>")
        fig.update_layout(plot_template("Requests by status")["layout"], legend_orientation="h")
        fig.update_layout(height=270)
        fig.add_annotation(text=f"{len(data)}<br>total", x=0.5, y=0.5, showarrow=False, font={"size": 18, "color": "#043b7a"})
        return fig

    @render_widget
    def priority_chart():
        data = filtered_requests()
        if data.empty:
            fig = empty_chart("Priority mix")
            fig.update_layout(height=270)
            return fig

        counts = data["priority"].astype(str).value_counts()
        chart_data = pd.DataFrame(
            {
                "Priority": settings.priorities,
                "Requests": [int(counts.get(priority, 0)) for priority in settings.priorities],
            }
        )
        chart_data = chart_data[chart_data["Requests"] > 0]
        fig = px.pie(
            chart_data,
            names="Priority",
            values="Requests",
            hole=0.52,
            color="Priority",
            color_discrete_map=PRIORITY_COLORS,
            color_discrete_sequence=BG_COLORS,
        )
        fig.update_traces(textinfo="label+percent+value", hovertemplate="%{label}: %{value} requests<extra></extra>")
        fig.update_layout(plot_template("Priority mix")["layout"], legend_orientation="h")
        fig.update_layout(height=270)
        fig.add_annotation(text=f"{len(data)}<br>total", x=0.5, y=0.5, showarrow=False, font={"size": 18, "color": "#043b7a"})
        return fig

    @render_widget
    def monthly_priority_chart():
        data = filtered_requests()
        if data.empty or data["log_date"].dropna().empty:
            fig = empty_chart("Monthly requests by priority")
            fig.update_layout(height=360)
            return fig

        trend = (
            data.dropna(subset=["log_date"])
            .assign(
                MonthStart=lambda frame: frame["log_date"].dt.to_period("M").dt.to_timestamp(),
                Month=lambda frame: frame["log_date"].dt.to_period("M").dt.to_timestamp().dt.strftime("%b %Y"),
            )
            .groupby(["MonthStart", "Month", "priority"], as_index=False)
            .size()
            .rename(columns={"priority": "Priority", "size": "Requests"})
            .sort_values("MonthStart")
        )
        fig = px.bar(
            trend,
            x="Month",
            y="Requests",
            color="Priority",
            category_orders={"Priority": settings.priorities},
            color_discrete_map=PRIORITY_COLORS,
            color_discrete_sequence=BG_COLORS,
        )
        fig.update_layout(plot_template("Monthly requests by priority")["layout"], barmode="stack", legend_orientation="h")
        fig.update_layout(height=360)
        fig.update_yaxes(dtick=1, rangemode="tozero")
        fig.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y} requests<extra></extra>")
        return fig

    @render_widget
    def assignee_chart():
        data = filtered_requests()
        if data.empty:
            fig = empty_chart("Work assigned")
            fig.update_layout(height=270)
            return fig

        chart_data = data["developer_name"].value_counts().head(8).reset_index()
        chart_data.columns = ["Assignee", "Requests"]
        fig = px.bar(
            chart_data,
            x="Requests",
            y="Assignee",
            orientation="h",
            color="Requests",
            color_continuous_scale=["#e6f7fb", "#00aeef", "#043b7a"],
        )
        fig.update_layout(plot_template("Work assigned")["layout"], showlegend=False, coloraxis_showscale=False)
        fig.update_layout(height=270)
        fig.update_xaxes(dtick=1, rangemode="tozero")
        return fig

    @render.data_frame
    def recent_requests():
        data = filtered_requests()
        if data.empty:
            visible = pd.DataFrame(columns=["Created", "BU", "Platform", "Priority", "Title", "Assignee", "Status"])
        else:
            visible = data.sort_values("log_date", ascending=False).head(25).assign(
                Created=lambda frame: frame["log_date"].dt.strftime("%Y-%m-%d %H:%M"),
                BU=lambda frame: frame["buisness_unit"],
            )[["Created", "BU", "platfor", "priority", "title", "developer_name", "status"]]
            visible.columns = ["Created", "BU", "Platform", "Priority", "Title", "Assignee", "Status"]
        return render.DataGrid(visible, height="260px", filters=True)

    @render.ui
    def manage_details_view():
        row = selected_request()
        if row is None:
            return ui.div("Select a request from the dropdown above to view its details.", class_="empty-state")
        
        log_date_str = ""
        if "log_date" in row and not pd.isna(row["log_date"]):
            log_date_str = pd.to_datetime(row["log_date"]).strftime("%Y-%m-%d %H:%M")
        
        prio = str(row["priority"])
        status = str(row["status"])
        
        return ui.div(
            ui.div(
                ui.div(f"ID: {row['request_id']}", class_="detail-id"),
                ui.div(
                    ui.span(prio, class_=f"badge prio-{prio.lower()}"),
                    ui.span(status, class_=f"badge status-{status.replace(' ', '-').lower()}"),
                    class_="detail-badges"
                ),
                class_="detail-header"
            ),
            ui.h3(str(row["title"]), class_="detail-title"),
            
            ui.div(
                ui.div(
                    ui.div("Log Date", class_="detail-meta-label"),
                    ui.div(log_date_str, class_="detail-meta-val"),
                    class_="detail-meta-item"
                ),
                ui.div(
                    ui.div("Requester Email", class_="detail-meta-label"),
                    ui.div(str(row.get("requester", "N/A")), class_="detail-meta-val"),
                    class_="detail-meta-item"
                ),
                ui.div(
                    ui.div("Business Unit", class_="detail-meta-label"),
                    ui.div(str(row["buisness_unit"]), class_="detail-meta-val"),
                    class_="detail-meta-item"
                ),
                ui.div(
                    ui.div("Development Type", class_="detail-meta-label"),
                    ui.div(str(row["dev_type"]), class_="detail-meta-val"),
                    class_="detail-meta-item"
                ),
                ui.div(
                    ui.div("Platform", class_="detail-meta-label"),
                    ui.div(str(row["platfor"]), class_="detail-meta-val"),
                    class_="detail-meta-item"
                ),
                ui.div(
                    ui.div("Assigned Owner", class_="detail-meta-label"),
                    ui.div(str(row["developer_name"]), class_="detail-meta-val"),
                    class_="detail-meta-item"
                ),
                class_="detail-meta-grid"
            ),
            
            ui.div(
                ui.div("Description", class_="detail-section-label"),
                ui.div(str(row["description"]), class_="detail-description-text"),
                class_="detail-section"
            ),
            class_="request-details-card"
        )

    @render.ui
    def comment_history():
        row = selected_request()
        if row is None or not str(row["dev_comment"]).strip():
            return ui.div("No comments yet.", class_="empty-state")
        entries = [entry.strip() for entry in str(row["dev_comment"]).split("\n") if entry.strip()]
        return ui.div(*[ui.div(entry, class_="comment-entry") for entry in reversed(entries)], class_="comment-list")

    @reactive.effect
    def sync_manage_choices():
        ui.update_select("manage_request_id", choices=request_choices())

    @reactive.effect
    def load_selected_request():
        row = selected_request()
        if row is None:
            return
        ui.update_select("manage_status", selected=str(row["status"]))
        ui.update_select("manage_priority", selected=str(row["priority"]))
        ui.update_date("manage_expected_end_date", value=date_value(row["expected_end_date"]))

    @reactive.effect
    @reactive.event(input.submit_request)
    def submit_request():
        title = input.title().strip()
        description = input.description().strip()
        email = input.email().strip()
        if not title or not description or not email:
            ui.notification_show("Email, title, and description are required.", type="warning", duration=4)
            return

        if "@" not in email or "." not in email:
            ui.notification_show("Please enter a valid email address.", type="warning", duration=4)
            return

        data = requests()
        if data.empty:
            next_id = "REQ000001"
        else:
            req_ids = data["request_id"][data["request_id"].str.startswith("REQ", na=False)]
            if req_ids.empty:
                next_id = "REQ000001"
            else:
                max_num = req_ids.str.replace("REQ", "").astype(int).max()
                next_id = f"REQ{max_num + 1:06d}"

        request = new_request(
            request_id=next_id,
            buisness_unit=input.business_unit(),
            dev_type=input.development_type(),
            platfor=input.platform(),
            priority=input.priority(),
            expected_end_date=input.expected_end_date(),
            title=title,
            description=description,
            developer_name=selected_assignee(),
            requester=email,
        )
        try:
            store.add_request(request)
        except Exception as exc:
            ui.notification_show(f"Request could not be saved: {exc}", type="error", duration=7)
            return
        refresh_count.set(refresh_count() + 1)
        ui.notification_show(
            ui.div(
                ui.p("Request successfully logged!", style="font-weight: bold; margin-bottom: 8px;"),
                ui.p(f"Request ID: {request['request_id']}", style="margin-bottom: 4px; font-family: monospace; font-size: 0.85rem;"),
                ui.p(f"Log Date: {request['log_date']}", style="margin-bottom: 4px;"),
                ui.p(f"Assigned to: {request['developer_name']}", style="margin-bottom: 0;"),
            ),
            type="message",
            duration=10,
        )
        clear_form_fields()

    @reactive.effect
    @reactive.event(input.save_update)
    def save_update():
        request_id = input.manage_request_id()
        if not request_id:
            ui.notification_show("Select a request first.", type="warning", duration=4)
            return
        row = selected_request()
        if row is None:
            return
        updates = {
            "status": input.manage_status(),
            "priority": input.manage_priority(),
            "expected_end_date": input.manage_expected_end_date().isoformat(),
            "buisness_unit": str(row["buisness_unit"]),
            "dev_type": str(row["dev_type"]),
            "platfor": str(row["platfor"]),
            "developer_name": str(row["developer_name"]),
            "title": str(row["title"]),
            "description": str(row["description"]),
            "requester": str(row.get("requester", "")),
        }
        try:
            store.update_request(request_id, updates, input.manage_comment(), input.developer_name())
        except Exception as exc:
            ui.notification_show(f"Update failed: {exc}", type="error", duration=7)
            return
        ui.update_text_area("manage_comment", value="")
        refresh_count.set(refresh_count() + 1)
        ui.notification_show("Request updated.", type="message", duration=4)

    @reactive.effect
    @reactive.event(input.clear_filters)
    def clear_filters():
        ui.update_select("filter_business_unit", selected=ALL_FILTER)
        ui.update_select("filter_priority", selected=ALL_FILTER)
        ui.update_select("filter_status", selected=ALL_FILTER)
        ui.update_select("filter_platform", selected=ALL_FILTER)
        ui.update_select("filter_development_type", selected=ALL_FILTER)
        ui.update_date_range("filter_created", start=DEFAULT_FILTER_START, end=DEFAULT_FILTER_END)

    @reactive.effect
    @reactive.event(input.clear_form)
    def clear_form():
        clear_form_fields()

    def clear_form_fields():
        ui.update_text("email", value="")
        ui.update_text("title", value="")
        ui.update_text_area("description", value="")
        ui.update_date("expected_end_date", value=date.today() + timedelta(days=14))


def date_value(value) -> date:
    if pd.isna(value):
        return date.today()
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return date.today()
    return parsed.date()


def format_date(value) -> str:
    parsed = date_value(value)
    return parsed.strftime("%Y-%m-%d")


app = App(app_ui, server)
