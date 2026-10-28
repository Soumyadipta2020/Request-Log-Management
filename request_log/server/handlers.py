from __future__ import annotations

from datetime import date, datetime, timedelta
import asyncio

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
from request_log.server.charts import empty_chart, get_integer_dtick, plot_template
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

        @reactive.calc
        def manage_filtered_requests() -> pd.DataFrame:
            data = requests()
            if data.empty:
                return data

            filtered = data.copy()
            filters = {
                "buisness_unit": input.manage_filter_business_unit(),
                "priority": input.manage_filter_priority(),
                "status": input.manage_filter_status(),
                "platfor": input.manage_filter_platform(),
                "dev_type": input.manage_filter_development_type(),
                "requester": input.manage_filter_requester(),
                "developer_email": input.manage_filter_developer(),
            }
            for column, selected in filters.items():
                if selected and selected != ALL_FILTER:
                    filtered = filtered[filtered[column].astype(str) == selected]

            date_range = input.manage_filter_created()
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

                current_m_req = input.manage_filter_requester()
                current_m_dev = input.manage_filter_developer()
                ui.update_select("manage_filter_requester", choices=requesters, selected=current_m_req if current_m_req in requesters else ALL_FILTER)
                ui.update_select("manage_filter_developer", choices=developers, selected=current_m_dev if current_m_dev in developers else ALL_FILTER)

        @reactive.calc
        def request_choices() -> dict[str, str]:
            data = manage_filtered_requests()
            if data.empty:
                if requests().empty:
                    return {"": "No requests loaded"}
                return {"": "No requests match filters"}
            sorted_data = data.sort_values("log_date", ascending=False, na_position="last")
            return {
                row.request_id: f"[{row.request_id}] {str(row.title or '')[:70]} | {row.status} | {row.developer_email}"
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
            ui.update_navs("main_tabs", selected="Summary")

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

        @render.text
        def manage_filter_context():
            total = len(requests())
            if total == 0:
                return "No requests loaded"
            filtered_total = len(manage_filtered_requests())
            if filtered_total == 0:
                return f"0 of {total} requests match filters"
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
                fig.update_layout(height=280)
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
                hole=0.62,
                color="Status",
                color_discrete_map=STATUS_COLORS,
                color_discrete_sequence=BG_COLORS,
            )
            fig.update_traces(
                textinfo="label+value",
                textposition="outside",
                textfont=dict(size=11, color="#475569"),
                marker=dict(line=dict(color="#ffffff", width=2)),
                hovertemplate="<b>%{label}</b><br>Requests: <b>%{value:,}</b> (%{percent})<extra></extra>",
            )
            fig.update_layout(
                plot_template("Requests by status")["layout"],
                showlegend=False,
                height=280,
                margin=dict(l=15, r=15, t=18, b=18),
                hoverlabel=dict(
                    bgcolor="#0f172a",
                    font_size=12,
                    font_family="Inter, Segoe UI, sans-serif",
                    font_color="#ffffff",
                    bordercolor="#1e293b",
                ),
            )
            fig.add_annotation(
                text=f"<span style='font-size:22px; font-weight:850; color:#0f172a;'>{len(data)}</span><br><span style='font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.06em;'>total</span>",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        @render_widget
        def priority_chart():
            data = filtered_requests()
            if data.empty:
                fig = empty_chart("Priority mix")
                fig.update_layout(height=280)
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
                hole=0.62,
                color="Priority",
                category_orders={"Priority": ["High", "Medium", "Low"]},
                color_discrete_map=PRIORITY_COLORS,
            )
            fig.update_traces(
                textinfo="label+value",
                textposition="outside",
                textfont=dict(size=11, color="#475569"),
                marker=dict(line=dict(color="#ffffff", width=2)),
                hovertemplate="<b>%{label} Priority</b><br>Requests: <b>%{value:,}</b> (%{percent})<extra></extra>",
            )
            fig.update_layout(
                plot_template("Priority mix")["layout"],
                showlegend=False,
                height=280,
                margin=dict(l=15, r=15, t=18, b=18),
                hoverlabel=dict(
                    bgcolor="#0f172a",
                    font_size=12,
                    font_family="Inter, Segoe UI, sans-serif",
                    font_color="#ffffff",
                    bordercolor="#1e293b",
                ),
            )
            fig.add_annotation(
                text=f"<span style='font-size:22px; font-weight:850; color:#0f172a;'>{len(data)}</span><br><span style='font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.06em;'>total</span>",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig

        @render_widget
        def monthly_priority_chart():
            data = filtered_requests()
            if data.empty or data["log_date"].dropna().empty:
                fig = empty_chart("Monthly requests by priority")
                fig.update_layout(height=280)
                return fig

            valid_dates = data.dropna(subset=["log_date"]).copy()
            valid_dates["MonthStart"] = pd.to_datetime(valid_dates["log_date"].dt.strftime("%Y-%m-01"))
            valid_dates["Month"] = valid_dates["log_date"].dt.strftime("%b %Y")

            month_map = (
                valid_dates[["MonthStart", "Month"]]
                .drop_duplicates()
                .sort_values("MonthStart")
            )
            sorted_months = month_map["Month"].tolist()

            counts = (
                valid_dates.groupby(["Month", "priority"], as_index=False, observed=False)
                .size()
                .rename(columns={"priority": "Priority", "size": "Requests"})
            )

            # Ensure all (Month, Priority) combinations exist so unified hover shows all categories
            full_index = pd.MultiIndex.from_product(
                [sorted_months, settings.priorities],
                names=["Month", "Priority"]
            )
            trend = (
                counts.set_index(["Month", "Priority"])
                .reindex(full_index, fill_value=0)
                .reset_index()
            )

            monthly_totals = trend.groupby("Month")["Requests"].sum()
            max_requests = int(monthly_totals.max()) if not monthly_totals.empty else 1
            dtick = get_integer_dtick(max_requests)

            fig = px.bar(
                trend,
                x="Month",
                y="Requests",
                color="Priority",
                category_orders={
                    "Month": sorted_months,
                    "Priority": ["Low", "Medium", "High"],
                },
                color_discrete_map=PRIORITY_COLORS,
            )

            fig.update_traces(
                marker_cornerradius=6,
                hovertemplate="%{fullData.name}: %{y:,}<extra></extra>",
            )

            fig.update_layout(
                barmode="stack",
                bargap=0.38,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                height=280,
                font=dict(family="Inter, Segoe UI, sans-serif", size=12, color="#64748b"),
                margin=dict(l=42, r=16, t=34, b=30),
                showlegend=True,
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.04,
                    xanchor="right",
                    x=1.0,
                    title_text="",
                    font=dict(size=11, color="#475569", family="Inter, Segoe UI, sans-serif"),
                    traceorder="normal",
                    itemclick=False,
                    itemdoubleclick=False,
                ),
                hovermode="x unified",
                hoverlabel=dict(
                    bgcolor="#0f172a",
                    font_size=12,
                    font_family="Inter, Segoe UI, sans-serif",
                    font_color="#ffffff",
                    bordercolor="#1e293b",
                ),
            )
            fig.update_xaxes(
                showgrid=False,
                showline=True,
                linecolor="#e2e8f0",
                linewidth=1,
                tickfont=dict(size=11, color="#64748b"),
                title="",
            )
            fig.update_yaxes(
                showgrid=True,
                gridcolor="rgba(226, 232, 240, 0.75)",
                gridwidth=1,
                showline=False,
                tickfont=dict(size=11, color="#64748b"),
                dtick=dtick,
                tick0=0,
                rangemode="tozero",
                automargin=True,
                tickformat=",d",
                title="",
            )
            return fig

        @render_widget
        def platform_chart():
            data = filtered_requests()
            if data.empty:
                fig = empty_chart("Platform mix")
                fig.update_layout(height=280)
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
                hole=0.62,
                color="Platform",
                color_discrete_sequence=BG_COLORS,
            )
            fig.update_traces(
                textinfo="label+value",
                textposition="outside",
                textfont=dict(size=11, color="#475569"),
                marker=dict(line=dict(color="#ffffff", width=2)),
                hovertemplate="<b>%{label}</b><br>Requests: <b>%{value:,}</b> (%{percent})<extra></extra>",
            )
            fig.update_layout(
                plot_template("Platform mix")["layout"],
                showlegend=False,
                height=280,
                margin=dict(l=15, r=15, t=18, b=18),
                hoverlabel=dict(
                    bgcolor="#0f172a",
                    font_size=12,
                    font_family="Inter, Segoe UI, sans-serif",
                    font_color="#ffffff",
                    bordercolor="#1e293b",
                ),
            )
            fig.add_annotation(
                text=f"<span style='font-size:22px; font-weight:850; color:#0f172a;'>{len(data)}</span><br><span style='font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.06em;'>total</span>",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
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
            fig.update_traces(textposition="outside", marker_cornerradius=4, hovertemplate="<b>%{y}</b><br>Requests: <b>%{x:,}</b><extra></extra>")
            fig.update_layout(
                plot_template("Assigned Work")["layout"],
                showlegend=False,
                coloraxis_showscale=False,
                hoverlabel=dict(bgcolor="#0f172a", font_size=12, font_color="#ffffff", bordercolor="#1e293b"),
            )
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
            fig.update_traces(textposition="outside", marker_cornerradius=4, hovertemplate="<b>%{y}</b><br>Requests: <b>%{x:,}</b><extra></extra>")
            fig.update_layout(
                plot_template("Business Unit Mix")["layout"],
                showlegend=False,
                hoverlabel=dict(bgcolor="#0f172a", font_size=12, font_color="#ffffff", bordercolor="#1e293b"),
            )
            fig.update_layout(height=270)
            fig.update_xaxes(visible=False)
            fig.update_yaxes(title="")
            return fig

        current_modal_req_id = reactive.value("")

        @render.data_frame
        def recent_requests():
            data = filtered_requests()
            if data.empty:
                from request_log.storage import COLUMNS
                visible = pd.DataFrame(columns=COLUMNS)
            else:
                visible = data.sort_values("log_date", ascending=False).copy()

            def make_priority_pill(priority: str):
                p_raw = str(priority).strip()
                p_slug = p_raw.lower().replace(" ", "-")
                return ui.HTML(f'<span class="tbl-pill prio-{p_slug}">{p_raw}</span>')

            def make_status_pill(status: str):
                s_raw = str(status).strip()
                s_slug = s_raw.lower().replace(" ", "-")
                return ui.HTML(
                    f'<span class="tbl-pill status-{s_slug}">'
                    f'<span class="tbl-status-dot"></span>'
                    f'{s_raw}'
                    f'</span>'
                )

            def make_show_btn(req_id: str, field: str, label: str = "View", empty: bool = False):
                empty_cls = " empty" if empty else ""
                title_desc = f"Click to view {field}" if not empty else f"No {field} yet (click to view details)"
                return ui.HTML(
                    f'<button type="button" class="table-show-btn{empty_cls}" '
                    f'title="{title_desc}" '
                    f'onclick="Shiny.setInputValue(\'show_request_details_id\', \'{req_id}:{field}\', {{priority: \'event\'}}); event.stopPropagation();">'
                    f'<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -1px; margin-right: 3px;">'
                    f'<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>'
                    f'<circle cx="12" cy="12" r="3"></circle>'
                    f'</svg>'
                    f'<span>{label}</span>'
                    f'</button>'
                )

            raw_statuses = []
            if not visible.empty:
                def format_table_date(val) -> str:
                    if pd.isna(val) or not str(val).strip() or str(val).strip() in ("None", "nan", "NaT"):
                        return ""
                    try:
                        parsed = pd.to_datetime(val, errors="coerce")
                        if pd.notna(parsed):
                            return parsed.strftime("%Y-%m-%d")
                        return str(val).split(" ")[0].split("T")[0]
                    except Exception:
                        return str(val).split(" ")[0].split("T")[0]

                if "log_date" in visible.columns:
                    visible["log_date"] = visible["log_date"].apply(format_table_date)
                if "expected_end_date" in visible.columns:
                    visible["expected_end_date"] = visible["expected_end_date"].apply(format_table_date)
                
                id_col = []
                prio_col = []
                status_col = []
                desc_col = []
                comment_col = []
                for row in visible.itertuples():
                    rid = str(row.request_id)
                    raw_s = str(getattr(row, "status", ""))
                    raw_statuses.append(raw_s)
                    id_col.append(rid)
                    prio_col.append(make_priority_pill(getattr(row, "priority", "")))
                    status_col.append(make_status_pill(raw_s))
                    desc_col.append(make_show_btn(rid, "description", "View"))
                    
                    raw_comment = getattr(row, "dev_comment", "")
                    has_comment = bool(raw_comment and str(raw_comment).strip() and str(raw_comment).strip() not in ("None", "nan"))
                    comment_col.append(make_show_btn(rid, "comment", "View" if has_comment else "None", empty=not has_comment))
                
                visible["request_id"] = id_col
                visible["priority"] = prio_col
                visible["status"] = status_col
                visible["description"] = desc_col
                visible["dev_comment"] = comment_col

            column_mapping = {
                "request_id": "Request ID",
                "buisness_unit": "Business Unit",
                "platfor": "Platform",
                "dev_type": "Dev Type",
                "priority": "Priority",
                "title": "Title",
                "status": "Status",
                "requester": "Requester",
                "log_date": "Log Date",
                "description": "Description",
                "expected_end_date": "Expected End Date",
                "developer_email": "Developer Email",
                "dev_comment": "Dev Comment",
            }
            visible = visible.rename(columns=column_mapping)

            ordered_columns = [
                "Request ID",
                "Business Unit",
                "Platform",
                "Dev Type",
                "Priority",
                "Title",
                "Status",
                "Requester",
                "Log Date",
                "Description",
                "Expected End Date",
                "Developer Email",
                "Dev Comment",
            ]
            visible = visible[[c for c in ordered_columns if c in visible.columns]]

            # Soft row-wise coloring based on status
            status_row_styles = {
                "pending": {
                    "style": {"background-color": "rgba(0, 174, 239, 0.07)"},
                    "class": "row-status-pending",
                },
                "in progress": {
                    "style": {"background-color": "rgba(4, 59, 122, 0.065)"},
                    "class": "row-status-in-progress",
                },
                "hold": {
                    "style": {"background-color": "rgba(243, 182, 31, 0.09)"},
                    "class": "row-status-hold",
                },
                "completed": {
                    "style": {"background-color": "rgba(122, 193, 67, 0.08)"},
                    "class": "row-status-completed",
                },
                "cancelled": {
                    "style": {"background-color": "rgba(255, 138, 138, 0.09)"},
                    "class": "row-status-cancelled",
                },
            }

            table_styles = [
                {"cols": ["Request ID"], "class": "tbl-id-cell"}
            ]
            if not visible.empty and raw_statuses:
                status_rows: dict[str, list[int]] = {}
                for idx, s in enumerate(raw_statuses):
                    k = s.lower().strip()
                    if k in status_row_styles:
                        status_rows.setdefault(k, []).append(idx)

                for k, r_indices in status_rows.items():
                    table_styles.append({
                        "rows": r_indices,
                        "style": status_row_styles[k]["style"],
                        "class": status_row_styles[k]["class"],
                    })

            return render.DataGrid(visible, height="480px", filters=True, styles=table_styles)

        @render.download(
            filename=lambda: f"requests_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        def download_table():
            data = filtered_requests()
            if data.empty:
                from request_log.storage import COLUMNS
                export_df = pd.DataFrame(columns=COLUMNS)
            else:
                export_df = data.sort_values("log_date", ascending=False).copy()

            def format_csv_date(val) -> str:
                if pd.isna(val) or not str(val).strip() or str(val).strip() in ("None", "nan", "NaT"):
                    return ""
                try:
                    parsed = pd.to_datetime(val, errors="coerce")
                    if pd.notna(parsed):
                        return parsed.strftime("%Y-%m-%d")
                    return str(val).split(" ")[0].split("T")[0]
                except Exception:
                    return str(val).split(" ")[0].split("T")[0]

            if not export_df.empty:
                if "log_date" in export_df.columns:
                    export_df["log_date"] = export_df["log_date"].apply(format_csv_date)
                if "expected_end_date" in export_df.columns:
                    export_df["expected_end_date"] = export_df["expected_end_date"].apply(format_csv_date)

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
            export_df = export_df.rename(columns=column_mapping)
            cols_order = [c for c in column_mapping.values() if c in export_df.columns]
            export_df = export_df[cols_order]

            yield export_df.to_csv(index=False)

        @reactive.effect
        @reactive.event(input.show_request_details_id)
        def show_request_modal():
            raw_val = input.show_request_details_id()
            if not raw_val:
                return
            parts = str(raw_val).split(":", 1)
            req_id = parts[0]
            field = parts[1] if len(parts) > 1 else "details"
            current_modal_req_id.set(req_id)

            data = requests()
            if data.empty:
                return
            matching = data[data["request_id"] == req_id]
            if matching.empty:
                return
            row = matching.iloc[0]

            log_date_str = ""
            if "log_date" in row and not pd.isna(row["log_date"]):
                log_date_str = pd.to_datetime(row["log_date"]).strftime("%Y-%m-%d %H:%M")

            end_date_str = ""
            if "expected_end_date" in row and not pd.isna(row["expected_end_date"]):
                try:
                    end_date_str = pd.to_datetime(row["expected_end_date"]).strftime("%Y-%m-%d")
                except Exception:
                    end_date_str = str(row["expected_end_date"])

            prio = str(row.get("priority", "N/A"))
            status = str(row.get("status", "N/A"))
            title_text = str(row.get("title", ""))
            desc_text = str(row.get("description", ""))
            dev_comment_raw = str(row.get("dev_comment", "") or "").strip()

            comment_nodes = []
            if dev_comment_raw and dev_comment_raw not in ("None", "nan"):
                entries = [entry.strip() for entry in dev_comment_raw.split("\n") if entry.strip()]
                for entry in reversed(entries):
                    comment_nodes.append(ui.div(entry, class_="comment-entry", style="margin-bottom: 8px;"))
            else:
                comment_nodes.append(ui.div("No developer comments recorded yet.", class_="empty-state", style="margin-bottom: 8px;"))

            desc_style = "max-height: 180px; overflow-y: auto;"
            if field == "description":
                desc_style += " border-color: var(--bg-cyan); box-shadow: 0 0 0 2px rgba(0, 174, 239, 0.25);"

            comment_style = "max-height: 180px; overflow-y: auto; padding: 4px;"
            if field == "comment":
                comment_style += " border: 1px solid var(--bg-cyan); border-radius: 6px; box-shadow: 0 0 0 2px rgba(0, 174, 239, 0.25);"

            m = ui.modal(
                ui.div(
                    ui.div(
                        ui.div(f"ID: {row['request_id']}", class_="detail-id"),
                        ui.div(
                            ui.span(prio, class_=f"badge prio-{prio.lower()}"),
                            ui.span(status, class_=f"badge status-{status.replace(' ', '-').lower()}"),
                            class_="detail-badges",
                        ),
                        class_="detail-header",
                    ),
                    ui.h3(title_text, class_="detail-title", style="margin: 12px 0 16px;"),
                    ui.div(
                        ui.div(
                            ui.div("Log Date", class_="detail-meta-label"),
                            ui.div(log_date_str, class_="detail-meta-val"),
                            class_="detail-meta-item",
                        ),
                        ui.div(
                            ui.div("Expected End Date", class_="detail-meta-label"),
                            ui.div(end_date_str, class_="detail-meta-val"),
                            class_="detail-meta-item",
                        ),
                        ui.div(
                            ui.div("Requester", class_="detail-meta-label"),
                            ui.div(str(row.get("requester", "N/A")), class_="detail-meta-val"),
                            class_="detail-meta-item",
                        ),
                        ui.div(
                            ui.div("Developer Email", class_="detail-meta-label"),
                            ui.div(str(row.get("developer_email", "N/A")), class_="detail-meta-val"),
                            class_="detail-meta-item",
                        ),
                        ui.div(
                            ui.div("Business Unit", class_="detail-meta-label"),
                            ui.div(str(row.get("buisness_unit", "N/A")), class_="detail-meta-val"),
                            class_="detail-meta-item",
                        ),
                        ui.div(
                            ui.div("Platform", class_="detail-meta-label"),
                            ui.div(str(row.get("platfor", "N/A")), class_="detail-meta-val"),
                            class_="detail-meta-item",
                        ),
                        ui.div(
                            ui.div("Development Type", class_="detail-meta-label"),
                            ui.div(str(row.get("dev_type", "N/A")), class_="detail-meta-val"),
                            class_="detail-meta-item",
                        ),
                        class_="detail-meta-grid",
                    ),
                    ui.div(
                        ui.div("DESCRIPTION", class_="detail-section-label", style="font-weight: 800; margin-top: 14px;"),
                        ui.div(
                            desc_text if desc_text else "No description provided.",
                            class_="detail-description-text",
                            style=desc_style,
                        ),
                        class_="detail-section",
                    ),
                    ui.div(
                        ui.div("DEVELOPER COMMENTS", class_="detail-section-label", style="font-weight: 800; margin-top: 16px;"),
                        ui.div(
                            *comment_nodes,
                            style=comment_style,
                        ),
                        class_="detail-section",
                    ),
                ),
                title=f"Request Details — {row['request_id']}",
                footer=ui.div(
                    ui.input_action_button("btn_modal_open_manage", "Open in Manage Tab", class_="btn-primary compact-btn"),
                    ui.input_action_button("btn_modal_close", "Close", class_="btn-outline-secondary compact-btn"),
                    style="display: flex; justify-content: space-between; width: 100%; align-items: center;",
                ),
                size="l",
                easy_close=True,
            )
            ui.modal_show(m)

        @reactive.effect
        @reactive.event(input.btn_modal_close)
        def close_request_modal():
            ui.modal_remove()

        @reactive.effect
        @reactive.event(input.btn_modal_open_manage)
        def jump_to_manage_tab():
            req_id = current_modal_req_id()
            if req_id:
                clear_manage_filters_action()
                ui.update_select("manage_request_id", selected=req_id)
                ui.update_navs("main_tabs", selected="Manage")
            ui.modal_remove()

        @render.ui
        def manage_details_view():
            row = selected_request()
            if row is None:
                if not requests().empty and manage_filtered_requests().empty:
                    return ui.div("No requests match the selected filters. Clear or adjust filters above to view details.", class_="empty-state")
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
            choices = request_choices()
            current = input.manage_request_id()
            if choices and "" not in choices:
                if current in choices:
                    selected = current
                else:
                    selected = next(iter(choices.keys()))
            else:
                selected = ""
            ui.update_select("manage_request_id", choices=choices, selected=selected)

        @reactive.effect
        def load_selected_request():
            row = selected_request()
            if row is None:
                return
            ui.update_select("manage_status", selected=str(row["status"]))
            ui.update_select("manage_priority", selected=str(row["priority"]))
            ui.update_date("manage_expected_end_date", value=date_value(row["expected_end_date"]))
            ui.update_text("manage_log_date", value=str(row["log_date"]))

        submit_status = reactive.value({"state": "idle"})
        update_status = reactive.value({"state": "idle"})

        @render.ui
        def submit_modal_body():
            st = submit_status()
            state = st.get("state")
            if state == "submitting":
                return ui.div(
                    ui.div(class_="status-modal-spinner"),
                    ui.div("Submitting Request...", class_="status-modal-title"),
                    ui.div(
                        "Please wait while your request is being logged and assigned.",
                        class_="status-modal-subtitle",
                    ),
                    class_="status-modal-content",
                )
            elif state == "success":
                return ui.div(
                    ui.div(
                        ui.HTML(
                            '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
                            '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>'
                            '<polyline points="22 4 12 14.01 9 11.01"></polyline>'
                            '</svg>'
                        ),
                        class_="status-modal-icon-circle success",
                    ),
                    ui.div("Request Submitted Successfully!", class_="status-modal-title"),
                    ui.div("Your request has been logged and assigned.", class_="status-modal-subtitle"),
                    ui.div(
                        ui.div("REQUEST NUMBER", class_="status-modal-id-label"),
                        ui.div(st.get("request_id", ""), class_="status-modal-id-value"),
                        ui.div(
                            ui.div(
                                ui.span("Title:"),
                                ui.strong(st.get("title", "")[:45] + ("..." if len(st.get("title", "")) > 45 else "")),
                                class_="status-modal-detail-row",
                            ),
                            ui.div(
                                ui.span("Assigned to:"),
                                ui.strong(st.get("developer_email", "")),
                                class_="status-modal-detail-row",
                            ),
                            ui.div(
                                ui.span("Business Unit:"),
                                ui.strong(st.get("buisness_unit", "")),
                                class_="status-modal-detail-row",
                            ),
                            class_="status-modal-details",
                        ),
                        class_="status-modal-badge-card",
                    ),
                    ui.tags.button(
                        "OK",
                        type="button",
                        class_="status-modal-ok-btn",
                        data_bs_dismiss="modal",
                        data_dismiss="modal",
                        autofocus="autofocus",
                        onclick="""
                            var m = window.bootstrap && window.bootstrap.Modal.getInstance(document.getElementById('shiny-modal'));
                            if (m) { m.hide(); }
                            setTimeout(function() {
                                $('.modal-backdrop').remove();
                                $('#shiny-modal-wrapper').empty();
                                $('body').removeClass('modal-open').css({'overflow': '', 'padding-right': ''});
                            }, 50);
                            Shiny.setInputValue('submit_modal_closed', Math.random());
                        """,
                    ),
                    class_="status-modal-content",
                )
            elif state == "error":
                return ui.div(
                    ui.div(
                        ui.HTML(
                            '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
                            '<circle cx="12" cy="12" r="10"></circle>'
                            '<line x1="12" y1="8" x2="12" y2="12"></line>'
                            '<line x1="12" y1="16" x2="12.01" y2="16"></line>'
                            '</svg>'
                        ),
                        class_="status-modal-icon-circle error",
                    ),
                    ui.div("Submission Failed", class_="status-modal-title"),
                    ui.div(st.get("error", "An error occurred."), class_="status-modal-subtitle"),
                    ui.tags.button(
                        "OK",
                        type="button",
                        class_="status-modal-ok-btn",
                        data_bs_dismiss="modal",
                        data_dismiss="modal",
                        autofocus="autofocus",
                        onclick="""
                            var m = window.bootstrap && window.bootstrap.Modal.getInstance(document.getElementById('shiny-modal'));
                            if (m) { m.hide(); }
                            setTimeout(function() {
                                $('.modal-backdrop').remove();
                                $('#shiny-modal-wrapper').empty();
                                $('body').removeClass('modal-open').css({'overflow': '', 'padding-right': ''});
                            }, 50);
                            Shiny.setInputValue('submit_modal_closed', Math.random());
                        """,
                    ),
                    class_="status-modal-content",
                )
            return ui.div()

        @render.ui
        def update_modal_body():
            st = update_status()
            state = st.get("state")
            if state == "submitting":
                return ui.div(
                    ui.div(class_="status-modal-spinner"),
                    ui.div("Saving Update...", class_="status-modal-title"),
                    ui.div(
                        f"Please wait while request {st.get('request_id', '')} is being updated.",
                        class_="status-modal-subtitle",
                    ),
                    class_="status-modal-content",
                )
            elif state == "success":
                return ui.div(
                    ui.div(
                        ui.HTML(
                            '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
                            '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>'
                            '<polyline points="22 4 12 14.01 9 11.01"></polyline>'
                            '</svg>'
                        ),
                        class_="status-modal-icon-circle success",
                    ),
                    ui.div("Successfully Updated!", class_="status-modal-title"),
                    ui.div(f"Request {st.get('request_id', '')} has been updated successfully.", class_="status-modal-subtitle"),
                    ui.div(
                        ui.div("REQUEST UPDATED", class_="status-modal-id-label"),
                        ui.div(st.get("request_id", ""), class_="status-modal-id-value"),
                        ui.div(
                            ui.div(
                                ui.span("Status:"),
                                ui.strong(st.get("status_val", "")),
                                class_="status-modal-detail-row",
                            ),
                            ui.div(
                                ui.span("Priority:"),
                                ui.strong(st.get("priority", "")),
                                class_="status-modal-detail-row",
                            ),
                            ui.div(
                                ui.span("Expected End Date:"),
                                ui.strong(st.get("expected_end_date", "")),
                                class_="status-modal-detail-row",
                            ),
                            class_="status-modal-details",
                        ),
                        class_="status-modal-badge-card",
                    ),
                    ui.tags.button(
                        "OK",
                        type="button",
                        class_="status-modal-ok-btn",
                        data_bs_dismiss="modal",
                        data_dismiss="modal",
                        autofocus="autofocus",
                        onclick="""
                            var m = window.bootstrap && window.bootstrap.Modal.getInstance(document.getElementById('shiny-modal'));
                            if (m) { m.hide(); }
                            setTimeout(function() {
                                $('.modal-backdrop').remove();
                                $('#shiny-modal-wrapper').empty();
                                $('body').removeClass('modal-open').css({'overflow': '', 'padding-right': ''});
                            }, 50);
                            Shiny.setInputValue('update_modal_closed', Math.random());
                        """,
                    ),
                    class_="status-modal-content",
                )
            elif state == "error":
                return ui.div(
                    ui.div(
                        ui.HTML(
                            '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
                            '<circle cx="12" cy="12" r="10"></circle>'
                            '<line x1="12" y1="8" x2="12" y2="12"></line>'
                            '<line x1="12" y1="16" x2="12.01" y2="16"></line>'
                            '</svg>'
                        ),
                        class_="status-modal-icon-circle error",
                    ),
                    ui.div("Update Failed", class_="status-modal-title"),
                    ui.div(st.get("error", "An error occurred."), class_="status-modal-subtitle"),
                    ui.tags.button(
                        "OK",
                        type="button",
                        class_="status-modal-ok-btn",
                        data_bs_dismiss="modal",
                        data_dismiss="modal",
                        autofocus="autofocus",
                        onclick="""
                            var m = window.bootstrap && window.bootstrap.Modal.getInstance(document.getElementById('shiny-modal'));
                            if (m) { m.hide(); }
                            setTimeout(function() {
                                $('.modal-backdrop').remove();
                                $('#shiny-modal-wrapper').empty();
                                $('body').removeClass('modal-open').css({'overflow': '', 'padding-right': ''});
                            }, 50);
                            Shiny.setInputValue('update_modal_closed', Math.random());
                        """,
                    ),
                    class_="status-modal-content",
                )
            return ui.div()

        @reactive.effect
        @reactive.event(input.submit_request)
        async def submit_request():
            title = input.title().strip()
            description = input.description().strip()
            email = input.email().strip()
            if not title or not description or not email:
                ui.notification_show("Email, title, and description are required.", type="warning", duration=4)
                return

            if "@" not in email or "." not in email:
                ui.notification_show("Please enter a valid email address.", type="warning", duration=4)
                return

            submit_status.set({"state": "submitting"})
            ui.modal_show(
                ui.modal(
                    ui.output_ui("submit_modal_body"),
                    title=None,
                    footer=None,
                    easy_close=False,
                    size="m",
                )
            )
            await asyncio.sleep(0.4)

            data = requests()
            next_id = "REQ000001"
            if not data.empty and "request_id" in data.columns:
                req_series = data["request_id"].dropna().astype(str)
                req_ids = req_series[req_series.str.startswith("REQ")]
                if not req_ids.empty:
                    nums = pd.to_numeric(req_ids.str.replace("REQ", "", regex=False), errors="coerce").dropna()
                    if not nums.empty:
                        next_id = f"REQ{int(nums.max()) + 1:06d}"

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
                await asyncio.to_thread(store.add_request, request)
            except Exception as exc:
                submit_status.set({"state": "error", "error": f"Request could not be saved: {exc}"})
                return

            refresh_count.set(refresh_count() + 1)
            clear_form_fields()
            submit_status.set({
                "state": "success",
                "request_id": request["request_id"],
                "title": request["title"],
                "developer_email": request["developer_email"],
                "buisness_unit": request["buisness_unit"],
            })

        @reactive.effect
        @reactive.event(input.submit_modal_closed)
        def on_submit_modal_closed():
            ui.modal_remove()

        @reactive.effect
        @reactive.event(input.save_update)
        async def save_update():
            request_id = input.manage_request_id()
            if not request_id:
                ui.notification_show("Select a request first.", type="warning", duration=4)
                return
            row = selected_request()
            if row is None:
                ui.notification_show("Selected request details not found.", type="warning", duration=4)
                return

            update_status.set({"state": "submitting", "request_id": request_id})
            ui.modal_show(
                ui.modal(
                    ui.output_ui("update_modal_body"),
                    title=None,
                    footer=None,
                    easy_close=False,
                    size="m",
                )
            )
            await asyncio.sleep(0.4)

            author = input.developer_email().strip() or str(row.get("developer_email", "") or "")
            updates = {
                "status": input.manage_status(),
                "priority": input.manage_priority(),
                "expected_end_date": input.manage_expected_end_date().isoformat(),
                "log_date": input.manage_log_date(),
                "buisness_unit": str(row["buisness_unit"]),
                "dev_type": str(row["dev_type"]),
                "platfor": str(row["platfor"]),
                "developer_email": author,
                "title": str(row["title"]),
                "description": str(row["description"]),
                "requester": str(row.get("requester", "")),
            }
            try:
                await asyncio.to_thread(
                    store.update_request,
                    request_id,
                    updates,
                    input.manage_comment(),
                    author,
                )
            except Exception as exc:
                update_status.set({"state": "error", "error": f"Update failed: {exc}"})
                return

            ui.update_text_area("manage_comment", value="")
            refresh_count.set(refresh_count() + 1)
            update_status.set({
                "state": "success",
                "request_id": request_id,
                "status_val": updates["status"],
                "priority": updates["priority"],
                "expected_end_date": updates["expected_end_date"],
            })

        @reactive.effect
        @reactive.event(input.update_modal_closed)
        def on_update_modal_closed():
            ui.modal_remove()

        def clear_manage_filters_action():
            ui.update_select("manage_filter_business_unit", selected=ALL_FILTER)
            ui.update_select("manage_filter_priority", selected=ALL_FILTER)
            ui.update_select("manage_filter_status", selected=ALL_FILTER)
            ui.update_select("manage_filter_platform", selected=ALL_FILTER)
            ui.update_select("manage_filter_development_type", selected=ALL_FILTER)
            ui.update_select("manage_filter_requester", selected=ALL_FILTER)
            ui.update_select("manage_filter_developer", selected=ALL_FILTER)
            ui.update_date_range("manage_filter_created", start=DEFAULT_FILTER_START, end=DEFAULT_FILTER_END)

        @reactive.effect
        @reactive.event(input.manage_clear_filters)
        def clear_manage_filters():
            clear_manage_filters_action()

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
