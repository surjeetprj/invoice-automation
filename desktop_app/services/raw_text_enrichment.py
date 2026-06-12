from __future__ import annotations

"""Deterministic field enrichment from layout-preserved invoice text."""

import re
from typing import Any

from ..domain.parsing import parse_date

GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z][A-Z][A-Z0-9]\b", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[- ][A-Za-z]{3,9}[- ]\d{2,4})\b"
)
LINE_TABLE_HEADERS = (
    "description", "item", "hsn", "sac", "qty", "quantity", "rate", "amount",
    "taxable", "igst", "cgst", "sgst", "total",
)


def enrich_from_raw_text(data: dict[str, Any], raw_text: str) -> None:
    """Fill safe deterministic fields that are visibly present in raw PDF text."""
    enrich_shipping_from_text(data, raw_text)
    enrich_due_date_from_text(data, raw_text)


def enrich_shipping_from_text(data: dict[str, Any], raw_text: str) -> None:
    """Extract a visible Ship To block when structured AI output missed it."""
    if data.get("shipping_address") or not raw_text:
        return
    lines = extract_ship_to_lines(raw_text)
    if not lines:
        return

    gstin = None
    address_parts: list[str] = []
    shipping_name = None
    for line in lines:
        match = GSTIN_PATTERN.search(line)
        if match:
            gstin = match.group(0).upper()
            line = line[:match.start()].strip()
        if not line:
            continue
        if shipping_name is None and looks_like_company_name(line):
            shipping_name = line
        else:
            address_parts.append(line)

    if shipping_name and not data.get("shipping_name"):
        data["shipping_name"] = shipping_name
    if address_parts and not data.get("shipping_address"):
        data["shipping_address"] = clean_address_segment(" ".join(address_parts))
    if gstin and not data.get("shipping_gstin"):
        data["shipping_gstin"] = gstin


def extract_ship_to_lines(raw_text: str) -> list[str]:
    """Return candidate lines from the Ship To column or block."""
    source_lines = [line.rstrip() for line in raw_text.splitlines()]
    for index, line in enumerate(source_lines):
        lower = line.lower()
        if "ship to" not in lower and "shipped to" not in lower:
            continue
        ship_pos = lower.find("ship to")
        if ship_pos < 0:
            ship_pos = lower.find("shipped to")
        candidates: list[str] = []
        for next_line in source_lines[index + 1:index + 10]:
            if is_line_item_header(next_line):
                break
            fragment = next_line[ship_pos:].strip() if ship_pos < len(next_line) else next_line.strip()
            if not fragment and len(next_line) > 20:
                fragment = next_line[len(next_line) // 2:].strip()
            fragment = fragment.strip(" :-")
            if fragment:
                candidates.append(fragment)
        cleaned = [line for line in candidates if not is_section_noise(line)]
        if cleaned:
            return cleaned
    return []


def enrich_due_date_from_text(data: dict[str, Any], raw_text: str) -> None:
    """Extract due date from common labels when Gemini leaves it empty."""
    if data.get("due_date") or not raw_text:
        return
    patterns = (
        r"(?i)\b(?:due date|payment due date|valid upto|valid up to)\b\s*[:\-]?\s*([^\n\r]{1,40})",
        r"(?i)\b(?:terms)\b\s*[:\-]?\s*due on receipt",
    )
    for pattern in patterns:
        match = re.search(pattern, raw_text)
        if not match:
            continue
        if match.lastindex:
            date_match = DATE_PATTERN.search(match.group(1))
            if date_match:
                parsed = parse_date(date_match.group(1))
                if parsed:
                    data["due_date"] = parsed.strftime("%d-%m-%Y")
                    return


def is_line_item_header(line: str) -> bool:
    """Detect the start of invoice item/tax tables."""
    lower = line.lower()
    return sum(1 for header in LINE_TABLE_HEADERS if header in lower) >= 3


def is_section_noise(line: str) -> bool:
    """Return true for labels that are not address content."""
    lower = line.strip().lower()
    return lower in {"bill to", "ship to", "shipped to"} or lower.startswith("place of supply")


def looks_like_company_name(line: str) -> bool:
    """Heuristically detect legal names in address blocks."""
    upper = line.upper()
    tokens = ("PRIVATE", "PVT", "LIMITED", "LTD", "LLP", "INC", "CORPORATION", "COMPANY")
    return any(token in upper for token in tokens)


def clean_address_segment(text: str) -> str:
    """Collapse whitespace and remove repeated label punctuation from addresses."""
    return " ".join(text.replace(" :", ":").split()).strip(" :-")
