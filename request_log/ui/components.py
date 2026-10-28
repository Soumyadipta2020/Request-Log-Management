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
