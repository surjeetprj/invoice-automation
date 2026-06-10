from __future__ import annotations

"""Shared parsing helpers for numeric and date values."""

import re
from datetime import datetime
from typing import Any

CURRENCY_TOKENS = ("Rs.", "Rs", "INR", "USD", "EUR", "GBP")
DATE_FORMATS = (
    "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y",
    "%d.%m.%Y", "%Y.%m.%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y",
    "%B %d, %Y", "%d-%b-%Y", "%d-%B-%Y", "%d-%b-%y", "%d-%B-%y",
)


def parse_decimal(value: Any, *, empty_as_none: bool = False) -> float | None:
    """Parse a decimal amount while handling currency symbols and commas."""
    if value is None:
        return None if empty_as_none else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None if empty_as_none else 0.0
    for token in CURRENCY_TOKENS:
        text = text.replace(token, "")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return None if empty_as_none else 0.0
    return float(text)


def parse_date(value: str | None) -> datetime | None:
    """Parse common invoice date formats."""
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None
