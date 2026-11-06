from __future__ import annotations

from shiny import ui

def metric_card(label: str, value: str, note: str, icon_svg: str, tone: str = "blue") -> ui.Tag:
    return ui.div(
        ui.div(ui.tags.span(ui.HTML(icon_svg), class_=f"metric-icon {tone}"), ui.tags.span(label), class_="metric-label"),
        ui.div(value, class_="metric-value"),
        ui.div(note, class_="metric-note"),
        class_=f"metric-card {tone}",
    )


def section_header(title: str, subtitle: str = "", icon_svg: str | None = None) -> ui.Tag:
    title_box = [ui.div(title, class_="panel-title")]
    if subtitle:
        title_box.append(ui.div(subtitle, class_="panel-subtitle"))

    left_elements = []
    if icon_svg:
        left_elements.append(ui.div(ui.HTML(icon_svg), class_="panel-header-icon"))
    left_elements.append(ui.div(*title_box, class_="panel-title-group"))

    return ui.div(
        ui.div(*left_elements, class_="section-header-left"),
        class_="section-header",
    )


def sidebar_link(input_id: str, label: str, active: bool = False) -> ui.Tag:
    state = " active" if active else ""
    return ui.input_action_link(input_id, label, class_=f"side-item{state}")


from cluster_manager import (
    cluster_offline_modal,
    cluster_starting_modal,
    cluster_error_modal,
)


def session_inactive_modal() -> ui.Tag:
    return ui.modal(
        ui.div(
            ui.div(
                ui.div(
                    ui.HTML('''
                        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="10"></circle>
                            <polyline points="12 6 12 12 16 14"></polyline>
                        </svg>
                    '''),
                    class_="status-modal-icon-circle inactive",
                ),
                ui.div("Session Inactive", class_="status-modal-title"),
                ui.div(
                    "The Databricks cluster is offline and no activity was detected for 30 minutes. Background polling has been paused to conserve resources.",
                    class_="status-modal-subtitle",
                ),
                ui.div(
                    ui.input_action_button(
                        "btn_resume_session",
                        "Resume Session",
                        class_="btn-primary",
                        style="min-width: 170px; font-weight: 700; padding: 10px 24px;",
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


def logout_confirmation_modal(user_email: str = "", databricks_host: str = "") -> ui.Tag:
    user_info = ui.div(
        ui.div("LOGGED IN AS", class_="status-modal-id-label"),
        ui.div(user_email, class_="status-modal-id-value", style="color: #1a202c; font-size: 1.05rem; font-weight: 600;"),
        class_="status-modal-badge-card",
        style="padding: 10px 18px; margin-bottom: 18px;",
    ) if user_email else None

    if databricks_host:
        clean_host = databricks_host if databricks_host.startswith("http") else f"https://{databricks_host}"
        databricks_logout_url = f"{clean_host.rstrip('/')}/login.html"
    else:
        databricks_logout_url = "/login.html"

    return ui.modal(
        ui.div(
            ui.div(
                ui.div(
                    ui.HTML('''
                        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                            <polyline points="16 17 21 12 16 7"></polyline>
                            <line x1="21" y1="12" x2="9" y2="12"></line>
                        </svg>
                    '''),
                    class_="status-modal-icon-circle logout-icon",
                ),
                ui.div("Log Out or End Your Session", class_="status-modal-title"),
                ui.div(
                    "You can end your active application session or sign out of your Databricks workspace completely.",
                    class_="status-modal-subtitle",
                ),
                user_info,
                ui.div(
                    ui.div(
                        ui.div(
                            ui.HTML('''
                                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--bg-blue); margin-bottom: 8px;">
                                    <circle cx="12" cy="12" r="10"></circle>
                                    <line x1="10" y1="15" x2="10" y2="9"></line>
                                    <line x1="14" y1="15" x2="14" y2="9"></line>
                                </svg>
                            '''),
                            ui.div("End App Session", style="font-weight: 700; color: #1e293b; margin-bottom: 4px; font-size: 0.95rem;"),
                            ui.div("Pause background compute polling and lock your session without losing login credentials.", style="font-size: 0.8rem; color: var(--muted); margin-bottom: 12px; line-height: 1.35; flex-grow: 1;"),
                            ui.input_action_button(
                                "btn_confirm_end_session",
                                "End Session",
                                class_="btn-primary compact-btn",
                                style="width: 100%; font-weight: 700;",
                            ),
                            class_="session-option-box",
                        ),
                        ui.div(
                            ui.HTML('''
                                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: #dc2626; margin-bottom: 8px;">
                                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                                    <polyline points="16 17 21 12 16 7"></polyline>
                                    <line x1="21" y1="12" x2="9" y2="12"></line>
                                </svg>
                            '''),
                            ui.div("Log Out of Databricks", style="font-weight: 700; color: #1e293b; margin-bottom: 4px; font-size: 0.95rem;"),
                            ui.div("Sign out completely from your Databricks workspace and close this application.", style="font-size: 0.8rem; color: var(--muted); margin-bottom: 12px; line-height: 1.35; flex-grow: 1;"),
                            ui.tags.a(
                                "Log Out",
                                href=databricks_logout_url,
                                target="_top",
                                class_="btn btn-outline-danger compact-btn",
                                style="width: 100%; font-weight: 700; text-align: center; text-decoration: none; display: inline-flex; align-items: center; justify-content: center;",
                            ),
                            class_="session-option-box",
                        ),
                        class_="session-options-row",
                    ),
                    style="margin-bottom: 16px; width: 100%;",
                ),
                ui.div(
                    ui.input_action_button(
                        "btn_dismiss_logout_modal",
                        "Cancel",
                        class_="btn-outline-secondary",
                        style="min-width: 100px; padding: 8px 22px;",
                    ),
                    style="display: flex; justify-content: center;",
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


def session_ended_modal(databricks_host: str = "") -> ui.Tag:
    if databricks_host:
        clean_host = databricks_host if databricks_host.startswith("http") else f"https://{databricks_host}"
        databricks_logout_url = f"{clean_host.rstrip('/')}/login.html"
    else:
        databricks_logout_url = "/login.html"

    return ui.modal(
        ui.div(
            ui.div(
                ui.div(
                    ui.HTML('''
                        <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                            <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                        </svg>
                    '''),
                    class_="status-modal-icon-circle inactive",
                ),
                ui.div("Session Ended", class_="status-modal-title"),
                ui.div(
                    "Your session has been ended and compute polling is paused. You can resume your session at any time or sign out completely.",
                    class_="status-modal-subtitle",
                ),
                ui.div(
                    ui.input_action_button(
                        "btn_resume_session",
                        "Resume Session",
                        class_="btn-primary",
                        style="min-width: 160px; font-weight: 700; padding: 10px 22px;",
                    ),
                    ui.tags.a(
                        "Log Out of Databricks",
                        href=databricks_logout_url,
                        target="_top",
                        class_="btn btn-outline-danger",
                        style="min-width: 160px; font-weight: 700; padding: 10px 22px; text-decoration: none; display: inline-flex; align-items: center; justify-content: center;",
                    ),
                    style="display: flex; justify-content: center; gap: 12px; margin-top: 10px;",
                ),
                ui.div(
                    "You may also safely close this browser tab.",
                    style="font-size: 0.78rem; color: var(--muted); margin-top: 16px; text-align: center;",
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
