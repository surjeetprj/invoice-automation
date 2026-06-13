from __future__ import annotations

"""Deterministic invoice normalization after AI extraction."""

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
