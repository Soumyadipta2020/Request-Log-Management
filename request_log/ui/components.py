from __future__ import annotations

from shiny import ui

def metric_card(label: str, value: str, note: str, icon_svg: str, tone: str = "blue") -> ui.Tag:
    return ui.div(
        ui.div(ui.tags.span(ui.HTML(icon_svg), class_=f"metric-icon {tone}"), ui.tags.span(label), class_="metric-label"),
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

