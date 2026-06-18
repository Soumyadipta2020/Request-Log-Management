from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
from shiny import Inputs, Outputs, Session, reactive, render, ui
from shinywidgets import render_widget

from request_log.assignments import assign_owner
from request_log.constants import (
    ALL_FILTER,
    BG_COLORS,
    DEFAULT_FILTER_END,
    DEFAULT_FILTER_START,
    PRIORITY_COLORS,
    STATUS_COLORS,
    SVG_CANCELLED,
    SVG_COMPLETED,
    SVG_HOLD,
    SVG_IN_PROGRESS,
    SVG_PENDING,
    SVG_TOTAL,
)
from request_log.server.charts import empty_chart, plot_template
from request_log.storage import COLUMNS, ClusterStartingError, empty_requests, new_request
from request_log.ui.components import metric_card, sidebar_link


def create_server(settings, store):
    def server(input: Inputs, output: Outputs, session: Session):
        refresh_count = reactive.value(0)
        request_data = reactive.value(empty_requests())
        data_loaded = reactive.value(False)
        data_error = reactive.value("")
        cluster_info = reactive.value(store.cluster_health())

        @reactive.effect
        @reactive.event(input.refresh)
        def refresh_data():
            refresh_count.set(refresh_count() + 1)

        @reactive.effect
        def poll_cluster_health():
            if settings.storage_mode == "databricks":
                reactive.invalidate_later(15, session=session)
            else:
                return

            try:
                print("[DEBUG] Calling store.cluster_health()...", flush=True)
                health = store.cluster_health()
                print(f"[DEBUG] Health result: {health}", flush=True)
                cluster_info.set(health)
                if health.get("can_read") == "true":
                    with reactive.isolate():
                        has_loaded_data = data_loaded()
                    if not has_loaded_data:
                        refresh_count.set(refresh_count() + 1)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                error_msg = f"{type(exc).__name__}: {str(exc)}"
                cluster_info.set({"state": "ERROR", "message": error_msg, "can_read": "false"})

        @reactive.effect
        def load_requests():
            refresh_count()
            try:
                health = store.cluster_health()
                cluster_info.set(health)
                if health.get("can_read") != "true":
                    data_error.set(health.get("message", "Databricks cluster is not ready."))
                    return
                print("[DEBUG] Calling store.read_requests()...", flush=True)
                data = store.read_requests()
                print(f"[DEBUG] read_requests returned {len(data)} rows.", flush=True)
                request_data.set(data)
                data_loaded.set(True)
                data_error.set("")
            except ClusterStartingError as exc:
                data_error.set(str(exc))
                cluster_info.set({"state": exc.state, "message": str(exc), "can_read": "false"})
            except Exception as exc:
                import traceback
                traceback.print_exc()
                error_msg = f"{type(exc).__name__}: {str(exc)}"
                data_error.set(error_msg)
                cluster_info.set({"state": "ERROR", "message": error_msg, "can_read": "false"})

        @reactive.calc
        def requests() -> pd.DataFrame:
            return request_data()

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
                "requester": input.filter_requester(),
                "developer_email": input.filter_developer(),
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

        @reactive.effect
        def update_dynamic_filters():
            data = requests()
            if not data.empty:
                requesters = [ALL_FILTER] + sorted([str(x) for x in data["requester"].dropna().unique() if str(x).strip()])
                developers = [ALL_FILTER] + sorted([str(x) for x in data["developer_email"].dropna().unique() if str(x).strip()])
                
                # We retain the currently selected value if it's still in the list, otherwise revert to ALL_FILTER
                current_req = input.filter_requester()
                current_dev = input.filter_developer()
                
                ui.update_select("filter_requester", choices=requesters, selected=current_req if current_req in requesters else ALL_FILTER)
                ui.update_select("filter_developer", choices=developers, selected=current_dev if current_dev in developers else ALL_FILTER)

        @reactive.calc
        def request_choices() -> dict[str, str]:
            data = requests()
            if data.empty:
                return {"": "No requests loaded"}
            sorted_data = data.sort_values("log_date", ascending=False)
            return {
                row.request_id: f"[{row.request_id}] {row.title[:70]} | {row.status} | {row.developer_email}"
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
            active_tab = input.main_tabs() or "Summary"
            return ui.div(
                sidebar_link("nav_visuals", "Summary", active_tab == "Summary"),
                sidebar_link("nav_log_request", "Log Request", active_tab == "Log Request"),
                sidebar_link("nav_manage", "Manage", active_tab == "Manage"),
                class_="side-nav",
            )

        @reactive.effect
        @reactive.event(input.nav_visuals)
        def show_visuals():
            ui.update_navset("main_tabs", selected="Summary")

        @reactive.effect
        @reactive.event(input.nav_log_request)
        def show_log_request():
            ui.update_navset("main_tabs", selected="Log Request")

        @reactive.effect
        @reactive.event(input.nav_manage)
        def show_manage():
            ui.update_navset("main_tabs", selected="Manage")

        @render.ui
        def cluster_banner():
            info = cluster_info()
            state = info.get("state", "LOCAL")
            message = info.get("message", "")
            if settings.storage_mode != "databricks" or state == "RUNNING":
                return None
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
                metric_card("Total Requests", str(total), "All logged demand", SVG_TOTAL, "blue"),
                metric_card("Pending", str(pending), "Awaiting action", SVG_PENDING, "cyan"),
                metric_card("In Progress", str(in_progress), "Being delivered", SVG_IN_PROGRESS, "navy"),
                metric_card("Hold", str(hold), "Paused or blocked", SVG_HOLD, "amber"),
                metric_card("Completed", str(completed), "Closed successfully", SVG_COMPLETED, "green"),
                metric_card("Cancelled", str(cancelled), "Stopped requests", SVG_CANCELLED, "red"),
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
            fig.update_traces(textinfo="label+value", textposition="outside", hovertemplate="%{label}: %{value} requests<extra></extra>")
            fig.update_layout(plot_template("Requests by status")["layout"], showlegend=False)
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
            fig.update_traces(textinfo="label+value", textposition="outside", hovertemplate="%{label}: %{value} requests<extra></extra>")
            fig.update_layout(plot_template("Priority mix")["layout"], showlegend=False)
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
                    MonthStart=lambda frame: pd.to_datetime(frame["log_date"].dt.strftime("%Y-%m-01")),
                    Month=lambda frame: frame["log_date"].dt.strftime("%b %Y"),
                )
                .groupby(["MonthStart", "Month", "priority"], as_index=False, observed=False)
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
            fig.update_layout(plot_template("Monthly requests")["layout"], barmode="stack", showlegend=False)
            fig.update_layout(height=270)
            fig.update_yaxes(dtick=1, rangemode="tozero")
            fig.update_traces(hovertemplate="%{x}<br>%{fullData.name}: %{y} requests<extra></extra>")
            return fig

        @render_widget
        def platform_chart():
            data = filtered_requests()
            if data.empty:
                fig = empty_chart("Platform mix")
                fig.update_layout(height=270)
                return fig

            counts = data["platfor"].astype(str).value_counts()
            chart_data = pd.DataFrame(
                {
                    "Platform": settings.platforms,
                    "Requests": [int(counts.get(platform, 0)) for platform in settings.platforms],
                }
            )
            chart_data = chart_data[chart_data["Requests"] > 0]
            fig = px.pie(
                chart_data,
                names="Platform",
                values="Requests",
                hole=0.52,
                color="Platform",
                color_discrete_sequence=BG_COLORS,
            )
            fig.update_traces(textinfo="label+value", textposition="outside", hovertemplate="%{label}: %{value} requests<extra></extra>")
            fig.update_layout(plot_template("Platform mix")["layout"], showlegend=False)
            fig.update_layout(height=270)
            fig.add_annotation(text=f"{len(data)}<br>total", x=0.5, y=0.5, showarrow=False, font={"size": 18, "color": "#043b7a"})
            return fig

        @render_widget
        def assignee_chart():
            data = filtered_requests()
            if data.empty:
                fig = empty_chart("Assigned Work")
                fig.update_layout(height=270)
                return fig

            chart_data = data["developer_email"].value_counts().head(8).reset_index()
            chart_data.columns = ["Assignee", "Requests"]
            fig = px.bar(
                chart_data,
                x="Requests",
                y="Assignee",
                orientation="h",
                color="Requests",
                color_continuous_scale=["#e6f7fb", "#00aeef", "#043b7a"],
                text="Requests"
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(plot_template("Assigned Work")["layout"], showlegend=False, coloraxis_showscale=False)
            fig.update_layout(height=270)
            fig.update_xaxes(visible=False)
            fig.update_yaxes(title="")
            return fig

        @render_widget
        def business_unit_chart():
            data = filtered_requests()
            if data.empty:
                fig = empty_chart("Business Unit Mix")
                fig.update_layout(height=270)
                return fig

            chart_data = data["buisness_unit"].value_counts().head(8).reset_index()
            chart_data.columns = ["Business Unit", "Requests"]
            fig = px.bar(
                chart_data,
                x="Requests",
                y="Business Unit",
                orientation="h",
                color="Business Unit",
                color_discrete_sequence=BG_COLORS,
                text="Requests"
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(plot_template("Business Unit Mix")["layout"], showlegend=False)
            fig.update_layout(height=270)
            fig.update_xaxes(visible=False)
            fig.update_yaxes(title="")
            return fig

        @render.data_frame
        def recent_requests():
            data = filtered_requests()
            if data.empty:
                from request_log.storage import COLUMNS
                visible = pd.DataFrame(columns=COLUMNS)
            else:
                visible = data.sort_values("log_date", ascending=False).head(25)
            
            column_mapping = {
                "request_id": "Request ID",
                "requester": "Requester",
                "buisness_unit": "Business Unit",
                "platfor": "Platform",
                "dev_type": "Dev Type",
                "priority": "Priority",
                "log_date": "Log Date",
                "expected_end_date": "Expected End Date",
                "title": "Title",
                "description": "Description",
                "developer_email": "Developer Email",
                "status": "Status",
                "dev_comment": "Dev Comment",
            }
            visible = visible.rename(columns=column_mapping)
            
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
                        ui.div("Developer Email", class_="detail-meta-label"),
                        ui.div(str(row["developer_email"]), class_="detail-meta-val"),
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
            ui.update_text("manage_log_date", value=str(row["log_date"]))

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
                developer_email=selected_assignee(),
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
                    ui.p(f"Assigned to: {request['developer_email']}", style="margin-bottom: 0;"),
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
                "log_date": input.manage_log_date(),
                "buisness_unit": str(row["buisness_unit"]),
                "dev_type": str(row["dev_type"]),
                "platfor": str(row["platfor"]),
                "developer_email": str(row["developer_email"]),
                "title": str(row["title"]),
                "description": str(row["description"]),
                "requester": str(row.get("requester", "")),
            }
            try:
                store.update_request(request_id, updates, input.manage_comment(), input.developer_email())
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
            ui.update_select("filter_requester", selected=ALL_FILTER)
            ui.update_select("filter_developer", selected=ALL_FILTER)
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

    return server


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
