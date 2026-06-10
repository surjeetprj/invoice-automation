from __future__ import annotations

"""Small formatting helpers used by UI pages."""

from typing import Any


def format_confidence(value: Any) -> str:
    """Format confidence as a percentage for display."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    if numeric <= 1:
        numeric *= 100
    return f"{numeric:.0f}%"


def format_money(value: Any) -> str:
    """Format numeric money values for display."""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "--"


def clear_layout(layout) -> None:
    """Delete all child widgets/layouts from a Qt layout."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())
