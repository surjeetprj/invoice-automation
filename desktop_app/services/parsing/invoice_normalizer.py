from __future__ import annotations

"""Deterministic invoice normalization after AI extraction."""

import re
from typing import Any

from ...config import CURRENCY_DECIMAL_PLACES, MATH_TOLERANCE, STATE_CODES
from ...domain.schemas import SupplyType
from .gst_normalization import normalize_tax_components, normalize_tax_totals, supply_type_value, to_float
from .visual_reconciliation import reconcile_visual_line_items


def normalize_extracted_data(data: dict[str, Any], document_kind: str | None = None) -> dict[str, Any]:
    """Normalize AI output into internally consistent invoice data."""
    normalize_gstin_fields(data)
    normalize_header_fields(data)
    normalize_numeric_fields(data)
    enrich_line_item_identity(data)
    normalize_discounted_line_values(data)
    normalize_tax_components(data)
    normalize_tax_totals(data)
    reconcile_visual_line_items(data, document_kind)
    return data


def normalize_gstin_fields(data: dict[str, Any]) -> None:
    """Clean GSTIN fields and derive state/PAN values."""
    for field in ("vendor_gstin", "customer_gstin", "shipping_gstin"):
        if isinstance(data.get(field), str):
            data[field] = data[field].strip().upper().replace(" ", "")

    vendor_gstin = data.get("vendor_gstin") or ""
    customer_gstin = data.get("customer_gstin") or ""
    if len(vendor_gstin) == 15:
        data["vendor_state_code"] = vendor_gstin[:2]
        data["vendor_pan"] = vendor_gstin[2:12]
    if len(customer_gstin) == 15:
        data["customer_state_code"] = customer_gstin[:2]
        data["customer_pan"] = customer_gstin[2:12]


def normalize_header_fields(data: dict[str, Any]) -> None:
    """Derive supply type and place of supply from normalized header fields."""
    if data.get("vendor_state_code") and data.get("customer_state_code"):
        data["supply_type"] = (
            SupplyType.INTRA_STATE.value
            if data["vendor_state_code"] == data["customer_state_code"]
            else SupplyType.INTER_STATE.value
        )
    else:
        data["supply_type"] = supply_type_value(data.get("supply_type"))
    if not data.get("place_of_supply") and data.get("customer_state_code") in STATE_CODES:
        data["place_of_supply"] = STATE_CODES[data["customer_state_code"]]


def normalize_numeric_fields(data: dict[str, Any]) -> None:
    """Convert numeric invoice and line-item fields into floats."""
    for field in (
        "total_taxable_amount", "total_cgst", "total_sgst", "total_igst",
        "total_cess", "total_tax_amount", "round_off", "total_amount",
    ):
        data[field] = to_float(data.get(field))

    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        for field in ("quantity", "rate", "discount", "taxable_value", "cess_amount", "total"):
            item[field] = to_float(item.get(field))
        for tax in item.get("taxes") or []:
            if not isinstance(tax, dict):
                continue
            tax["tax_type"] = str(tax.get("tax_type") or "").strip().upper()
            for field in ("tax_rate", "taxable_amount", "tax_amount"):
                tax[field] = to_float(tax.get(field))


def enrich_line_item_identity(data: dict[str, Any]) -> None:
    """Fill missing item names, HSN/SAC codes, and units from visible descriptions."""
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        if not description:
            continue
        if not useful_text(item.get("hsn_sac")):
            hsn_sac = extract_hsn_sac(description)
            if hsn_sac:
                item["hsn_sac"] = hsn_sac
        if not useful_text(item.get("unit")):
            unit = extract_unit(description)
            if unit:
                item["unit"] = unit
        if not useful_text(item.get("unit")) and to_float(item.get("quantity")) > 0:
            item["unit"] = "PCS"
        if not useful_text(item.get("item_name")):
            item_name = extract_item_name(description)
            if item_name:
                item["item_name"] = item_name


def useful_text(value: Any) -> bool:
    """Return True when a value contains non-empty text."""
    return isinstance(value, str) and bool(value.strip())


def extract_hsn_sac(description: str) -> str | None:
    """Extract an embedded HSN/SAC code from line description text."""
    patterns = (
        r"\bHSN\s*/\s*SAC\s*(?:Code)?\s*[:\-]?\s*([0-9]{4,8})\b",
        r"\b(?:HSN|SAC)\s*(?:Code)?\s*[:\-]?\s*([0-9]{4,8})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, description, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def extract_unit(description: str) -> str | None:
    """Extract an explicit unit token from line description text."""
    normalized = " ".join(description.replace("|", " ").split())
    patterns = (
        (r"\b\d+(?:\.\d+)?\s*(years?|yrs?|yr)\b", "Year"),
        (r"\b\d+(?:\.\d+)?\s*(months?|mos?|month)\b", "Month"),
        (r"\b\d+(?:\.\d+)?\s*(licenses?|licence|licences)\b", "License"),
        (r"\b\d+(?:\.\d+)?\s*(users?)\b", "User"),
        (r"\b(?:unit|uom)\s*[:\-]?\s*(NOS|NO|PCS|PC|PRS|PAIR|PAIRS|KGS|KG|LTR|MTR)\b", None),
        (r"\b(NOS|NO|PCS|PC|PRS|PAIR|PAIRS|KGS|KG|LTR|MTR)\b", None),
    )
    for pattern, mapped in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        return mapped or match.group(1).upper()
    return None


def extract_item_name(description: str) -> str | None:
    """Extract a concise item/service name while preserving the full description elsewhere."""
    text = " ".join(description.split())
    if not text:
        return None
    metadata_pattern = (
        r"\b(?:HSN\s*/\s*SAC|HSN|SAC|Serial|Sr\.?\s*No|Username|User\s*Name|Folder\s*Name|"
        r"IP|From|Period|Dis\s*Price|PIN\s*Number)\b"
    )
    match = re.search(metadata_pattern, text, flags=re.IGNORECASE)
    candidate = text[:match.start()].strip(" :-,|") if match else text
    candidate = re.sub(r"\s+\d+(?:\.\d+)?\s*(?:years?|yrs?|yr|months?|mos?)\s*plan\b.*$", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+\d+\s*[|/]\s*\d+.*$", "", candidate).strip(" :-,|")
    if not candidate:
        candidate = text
    return candidate[:255].strip() or None


def normalize_discounted_line_values(data: dict[str, Any]) -> None:
    """Use visible discounts to align line taxable values with invoice totals."""
    items = [item for item in data.get("line_items") or [] if isinstance(item, dict)]
    if not items:
        return
    for item in items:
        quantity = item.get("quantity", 0.0)
        rate = item.get("rate", 0.0)
        discount = item.get("discount", 0.0)
        taxable_value = item.get("taxable_value", 0.0)
        gross_value = quantity * rate
        if item.get("total", 0.0) == 0.0 and item.get("taxable_value", 0.0) > 0.0:
            item["total"] = item["taxable_value"]
        if quantity > 0 and rate > 0 and discount == 0.0 and taxable_value > 0.0:
            inferred_discount = round(gross_value - taxable_value, CURRENCY_DECIMAL_PLACES)
            if inferred_discount > MATH_TOLERANCE:
                item["discount"] = inferred_discount
                discount = inferred_discount
        if quantity > 0 and rate > 0 and discount > 0:
            expected = round((quantity * rate) - discount, CURRENCY_DECIMAL_PLACES)
            if expected >= 0 and abs(item.get("taxable_value", 0.0) - expected) > 0.01:
                item["taxable_value"] = expected
                if item.get("total", 0.0) in {0.0, rate * quantity}:
                    item["total"] = expected
    invoice_taxable = data.get("total_taxable_amount", 0.0)
    if len(items) == 1 and invoice_taxable > 0 and abs(items[0].get("taxable_value", 0.0) - invoice_taxable) > 0.01:
        quantity = items[0].get("quantity", 0.0)
        rate = items[0].get("rate", 0.0)
        discount = items[0].get("discount", 0.0)
        expected = round((quantity * rate) - discount, CURRENCY_DECIMAL_PLACES) if quantity > 0 and rate > 0 else 0.0
        if abs(expected - invoice_taxable) <= 0.01:
            items[0]["taxable_value"] = invoice_taxable
