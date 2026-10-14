from __future__ import annotations

import math
import plotly.graph_objects as go


def get_integer_dtick(max_val: int | float, target_ticks: int = 4) -> int:
    """Calculate a clean integer tick interval (1, 2, 5, 10, 20, 50, ...)
    to avoid overcrowded y-axis labels while avoiding fractional ticks."""
    try:
        val = float(max_val)
        if math.isnan(val) or val <= target_ticks:
            return 1
    except (TypeError, ValueError):
        return 1

    raw_step = val / target_ticks
    magnitude = 10 ** int(math.floor(math.log10(raw_step)))
    fraction = raw_step / magnitude
    if fraction <= 1.5:
        step = 1 * magnitude
    elif fraction <= 3.0:
        step = 2 * magnitude
    elif fraction <= 7.0:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    return max(1, int(step))


def plot_template(title: str):
    return {
        "layout": {
            "paper_bgcolor": "rgba(0,0,0,0)",
            "plot_bgcolor": "rgba(0,0,0,0)",
            "font": {"family": "Inter, Segoe UI, Arial", "color": "#172033", "size": 12},
            "margin": {"l": 18, "r": 18, "t": 14, "b": 28},
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
