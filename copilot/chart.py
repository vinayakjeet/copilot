"""Auto-chart selection as a pure function.

Takes the shape of a result set and returns a chart descriptor, or None when
the honest answer is "this is a table, not a picture". No plotting library: the
UI draws from the descriptor with inline SVG.
"""

from __future__ import annotations

import datetime as dt
from typing import Any


def _classify(values: list[Any]) -> str:
    """One python type per column, decided by the first non-null sample."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (dt.date, dt.time)):
            return "temporal"
        if isinstance(value, (int, float)):
            return "numeric"
        return "text"
    return "empty"


def chart_descriptor(
    columns: list[str], rows: list[list[Any]], max_categories: int = 20
) -> dict | None:
    if not columns or not rows:
        return None

    kinds = [_classify([row[i] for row in rows]) for i in range(len(columns))]

    temporal = [i for i, k in enumerate(kinds) if k == "temporal"]
    numeric = [i for i, k in enumerate(kinds) if k == "numeric"]
    text = [i for i, k in enumerate(kinds) if k == "text"]

    # Time on one axis and something countable on the other is the classic
    # analyst question; line wins over bar because time is ordered.
    if temporal and numeric:
        return {
            "type": "line",
            "x": columns[temporal[0]],
            "y": [columns[i] for i in numeric[:3]],
        }

    if len(numeric) >= 2 and not text:
        return {
            "type": "scatter",
            "x": columns[numeric[0]],
            "y": [columns[numeric[1]]],
        }

    if text and numeric:
        distinct = {row[text[0]] for row in rows}
        if len(distinct) <= max_categories:
            return {
                "type": "bar",
                "x": columns[text[0]],
                "y": [columns[numeric[0]]],
            }
        return None

    return None


def chart_title(descriptor: dict | None, question: str) -> str | None:
    if descriptor is None:
        return None
    verb = {"line": "over time", "bar": "by", "scatter": "against"}[descriptor["type"]]
    y = descriptor["y"][0].replace("_", " ")
    if descriptor["type"] == "line":
        return f"{y} {verb}"
    x = descriptor["x"].replace("_", " ")
    if descriptor["type"] == "bar":
        return f"{y} by {x}"
    return f"{y} against {x}"
