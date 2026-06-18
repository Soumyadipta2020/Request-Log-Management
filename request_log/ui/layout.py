from __future__ import annotations

from datetime import date, timedelta

from shiny import ui
from shinywidgets import output_widget

from request_log.constants import ALL_FILTER, DEFAULT_FILTER_END, DEFAULT_FILTER_START
from request_log.ui.components import section_header


def create_app_ui(settings):
    return ui.page_fluid(
        ui.include_css("www/styles.css"),
        ui.div(
            ui.tags.aside(
                ui.div("BRITISH GAS", class_="logo"),
                ui.div("REQUESTS", class_="side-heading"),
                ui.output_ui("sidebar_nav"),

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
                        "Summary",
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
                                    section_header("Status Mix"),
                                    output_widget("status_chart"),
                                    class_="content-panel",
                                ),
                                ui.div(
                                    section_header("Platform Mix"),
                                    output_widget("platform_chart"),
                                    class_="content-panel",
                                ),
                                ui.div(
                                    section_header("Priority Mix"),
                                    output_widget("priority_chart"),
                                    class_="content-panel",
                                ),
                                ui.div(
                                    section_header("Monthly Requests", "Stacked by priority category"),
                                    output_widget("monthly_priority_chart"),
                                    class_="content-panel chart-full-width",
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
                                        ui.input_text("manage_log_date", "Log Date (YYYY-MM-DD HH:MM:SS)"),
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
                    selected="Summary",
                ),
                class_="main-workspace",
            ),
            class_="app-shell",
        ),
        title="British Gas Request Management",
        )
