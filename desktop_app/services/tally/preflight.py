from __future__ import annotations

"""TallyPrime master preflight and inventory posting helpers."""

from dataclasses import dataclass
from typing import Iterable
from xml.etree import ElementTree

from ...domain.schemas import InvoiceData
from .masters import STOCK_ITEM_MASTER, TallyMaster
from .responses import TallyResponse
from .vouchers import gst_amount_details


@dataclass(frozen=True)
class TallyPreflight:
    """Required and missing Tally masters for one invoice."""

    required_masters: tuple[TallyMaster, ...]
    missing_masters: tuple[TallyMaster, ...]

    @property
    def has_missing(self) -> bool:
        """Return True when master creation is needed before posting."""
        return bool(self.missing_masters)

    def missing_labels(self) -> list[str]:
        """Return display labels for missing masters."""
        return [master.label for master in self.missing_masters]


def parse_master_names(xml_text: str) -> set[str]:
    """Parse master names returned by a Tally collection export."""
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError:
        return set()
    names: set[str] = set()
    for element in root.iter():
        tag = element.tag.upper()
        attr_name = element.attrib.get("NAME") or element.attrib.get("Name")
        if attr_name:
            names.add(attr_name.strip())
        if tag in {"NAME", "LEDGERNAME", "VOUCHERTYPENAME"} and element.text and element.text.strip():
            names.add(element.text.strip())
    return names


def normalize_name(value: str) -> str:
    """Normalize a Tally master name for case-insensitive matching."""
    return " ".join(value.strip().lower().split())


def normalized_names(values: set[str]) -> set[str]:
    """Normalize a set of Tally master names for comparisons."""
    return {normalize_name(value) for value in values}


def prioritize_inventory_masters(masters: Iterable[TallyMaster]) -> tuple[TallyMaster, ...]:
    """Return inventory masters with stock items after their dependencies."""
    ordered = tuple(masters)
    return tuple(master for master in ordered if master.kind != STOCK_ITEM_MASTER) + tuple(
        master for master in ordered if master.kind == STOCK_ITEM_MASTER
    )


def merge_tally_responses(responses: Iterable[TallyResponse]) -> TallyResponse:
    """Combine multiple Tally responses into one aggregate result."""
    collected = tuple(responses)
    if not collected:
        return TallyResponse(success=True)
    messages: list[str] = []
    last_voucher_id: str | None = None
    raw_xml_parts: list[str] = []
    for response in collected:
        if response.last_voucher_id:
            last_voucher_id = response.last_voucher_id
        if response.raw_xml:
            raw_xml_parts.append(response.raw_xml)
        for message in response.messages:
            if message not in messages:
                messages.append(message)
    success = all(response.success for response in collected)
    return TallyResponse(
        success=success,
        created=sum(response.created for response in collected),
        altered=sum(response.altered for response in collected),
        errors=sum(response.errors for response in collected),
        exceptions=sum(response.exceptions for response in collected),
        cancelled=sum(response.cancelled for response in collected),
        last_voucher_id=last_voucher_id,
        messages=tuple(messages),
        raw_xml="\n".join(raw_xml_parts),
    )


def annotate_tally_response(response: TallyResponse, master_label: str) -> TallyResponse:
    """Add master context to a Tally response so failures name the exact master."""
    if response.messages:
        messages = tuple(f"{master_label} -> {message}" for message in response.messages)
    elif not response.success:
        messages = (f"{master_label} -> import failed",)
    else:
        messages = ()
    return TallyResponse(
        success=response.success,
        created=response.created,
        altered=response.altered,
        errors=response.errors,
        exceptions=response.exceptions,
        cancelled=response.cancelled,
        last_voucher_id=response.last_voucher_id,
        messages=messages,
        raw_xml=response.raw_xml,
    )


def validate_inventory_item_posting(data: InvoiceData) -> None:
    """Raise when reviewed line items are not complete enough for item export."""
    if not data.line_items:
        raise ValueError("Item posting requires at least one reviewed line item.")
    has_tax_detail = bool(gst_amount_details(data))
    issues: list[str] = []
    for index, item in enumerate(data.line_items, start=1):
        line_issues: list[str] = []
        if not ((item.item_name or item.description or "").strip()):
            line_issues.append("item name is missing")
        if item.quantity <= 0:
            line_issues.append("quantity must be greater than 0")
        if not (item.unit or "").strip():
            line_issues.append("unit is missing")
        if item.rate <= 0:
            line_issues.append("rate must be greater than 0")
        if item.taxable_value <= 0:
            line_issues.append("taxable value must be greater than 0")
        if not ((item.taxes and any(tax.tax_amount > 0 or tax.tax_rate > 0 for tax in item.taxes)) or has_tax_detail):
            line_issues.append("tax detail is missing")
        if line_issues:
            issues.append(f"Line {index}: " + ", ".join(line_issues))
    if issues:
        raise ValueError("Item posting requires complete reviewed line items.\n" + "\n".join(issues))
