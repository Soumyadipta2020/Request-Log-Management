from __future__ import annotations

import plotly.graph_objects as go

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
