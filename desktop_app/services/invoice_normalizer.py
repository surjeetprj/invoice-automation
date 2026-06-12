from __future__ import annotations

"""Deterministic invoice normalization after AI extraction."""

from typing import Any

from ..config import CURRENCY_DECIMAL_PLACES, MATH_TOLERANCE, STATE_CODES
from ..domain.parsing import parse_decimal
from ..domain.schemas import SupplyType

GENERIC_TAX_TYPES = {"", "GST", "TAX", "UTGST", "CGST/SGST", "CGST+SGST", "OUTPUT GST"}


def normalize_extracted_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize AI output into internally consistent invoice data."""
    normalize_gstin_fields(data)
    normalize_header_fields(data)
    normalize_numeric_fields(data)
    normalize_discounted_line_values(data)
    normalize_tax_components(data)
    normalize_tax_totals(data)
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


def normalize_tax_totals(data: dict[str, Any]) -> None:
    """Fill aggregate tax total when component tax totals are present."""
    component_tax_total = data["total_cgst"] + data["total_sgst"] + data["total_igst"] + data["total_cess"]
    if data["total_tax_amount"] == 0.0 and component_tax_total > 0.0:
        data["total_tax_amount"] = round(component_tax_total, CURRENCY_DECIMAL_PLACES)
    if data["total_tax_amount"] == 0.0:
        line_tax_total = sum_line_tax(data)
        if line_tax_total > 0.0:
            data["total_tax_amount"] = round(line_tax_total, CURRENCY_DECIMAL_PLACES)


def to_float(value: Any) -> float:
    """Convert common invoice amount values to float with shared parsing rules."""
    try:
        parsed = parse_decimal(value)
    except (TypeError, ValueError):
        return 0.0
    return float(parsed or 0.0)


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


def normalize_tax_components(data: dict[str, Any]) -> None:
    """Normalize generic tax rows into GST components based on supply type."""
    supply_type = supply_type_value(data.get("supply_type"))
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        taxes = [tax for tax in item.get("taxes") or [] if isinstance(tax, dict)]
        normalized: list[dict[str, Any]] = []
        for tax in taxes:
            tax_type = str(tax.get("tax_type") or "").strip().upper()
            if supply_type == SupplyType.INTER_STATE.value and tax_type in GENERIC_TAX_TYPES:
                tax["tax_type"] = "IGST"
                normalized.append(tax)
            elif supply_type == SupplyType.INTRA_STATE.value and tax_type in GENERIC_TAX_TYPES and tax.get("tax_amount", 0.0) > 0:
                half_rate = round(tax.get("tax_rate", 0.0) / 2, CURRENCY_DECIMAL_PLACES)
                half_amount = round(tax.get("tax_amount", 0.0) / 2, CURRENCY_DECIMAL_PLACES)
                normalized.extend([
                    {**tax, "tax_type": "CGST", "tax_rate": half_rate, "tax_amount": half_amount},
                    {**tax, "tax_type": "SGST", "tax_rate": half_rate, "tax_amount": half_amount},
                ])
            else:
                tax["tax_type"] = tax_type
                normalized.append(tax)
        item["taxes"] = normalized

    component_totals = {"CGST": 0.0, "SGST": 0.0, "IGST": 0.0}
    for item in data.get("line_items") or []:
        for tax in item.get("taxes") or []:
            tax_type = str(tax.get("tax_type") or "").upper()
            if tax_type in component_totals:
                component_totals[tax_type] += tax.get("tax_amount", 0.0)
    if data.get("total_cgst", 0.0) == 0.0 and component_totals["CGST"] > 0:
        data["total_cgst"] = round(component_totals["CGST"], CURRENCY_DECIMAL_PLACES)
    if data.get("total_sgst", 0.0) == 0.0 and component_totals["SGST"] > 0:
        data["total_sgst"] = round(component_totals["SGST"], CURRENCY_DECIMAL_PLACES)
    if data.get("total_igst", 0.0) == 0.0 and component_totals["IGST"] > 0:
        data["total_igst"] = round(component_totals["IGST"], CURRENCY_DECIMAL_PLACES)
    align_aggregate_taxes_with_supply_type(data, supply_type)


def align_aggregate_taxes_with_supply_type(data: dict[str, Any], supply_type: str) -> None:
    """Keep aggregate tax totals consistent with GSTIN-derived supply type."""
    total_cgst = data.get("total_cgst", 0.0)
    total_sgst = data.get("total_sgst", 0.0)
    total_igst = data.get("total_igst", 0.0)
    state_tax_total = total_cgst + total_sgst

    if supply_type == SupplyType.INTER_STATE.value and total_igst == 0.0 and state_tax_total > 0.0:
        convert_line_state_taxes_to_igst(data)
        data["total_igst"] = round(state_tax_total, CURRENCY_DECIMAL_PLACES)
        data["total_cgst"] = 0.0
        data["total_sgst"] = 0.0
    elif supply_type == SupplyType.INTRA_STATE.value and total_igst > 0.0 and state_tax_total > 0.0:
        if abs(total_igst - state_tax_total) <= MATH_TOLERANCE:
            data["total_igst"] = 0.0


def convert_line_state_taxes_to_igst(data: dict[str, Any]) -> None:
    """Convert line-level CGST/SGST taxes to IGST for inter-state invoices."""
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        taxes = [tax for tax in item.get("taxes") or [] if isinstance(tax, dict)]
        state_taxes = [tax for tax in taxes if str(tax.get("tax_type") or "").upper() in {"CGST", "SGST"}]
        other_taxes = [tax for tax in taxes if str(tax.get("tax_type") or "").upper() not in {"CGST", "SGST"}]
        if not state_taxes:
            continue
        taxable_amount = max((tax.get("taxable_amount", 0.0) for tax in state_taxes), default=item.get("taxable_value", 0.0))
        tax_amount = round(sum(tax.get("tax_amount", 0.0) for tax in state_taxes), CURRENCY_DECIMAL_PLACES)
        tax_rate = round(sum(tax.get("tax_rate", 0.0) for tax in state_taxes), CURRENCY_DECIMAL_PLACES)
        other_taxes.append({
            "tax_type": "IGST",
            "tax_rate": tax_rate,
            "taxable_amount": taxable_amount,
            "tax_amount": tax_amount,
        })
        item["taxes"] = other_taxes


def sum_line_tax(data: dict[str, Any]) -> float:
    """Return summed line-level tax amount."""
    total = 0.0
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        for tax in item.get("taxes") or []:
            if isinstance(tax, dict):
                total += to_float(tax.get("tax_amount"))
    return total


def supply_type_value(value: Any) -> str:
    """Return a normalized SupplyType string from enum or plain values."""
    if isinstance(value, SupplyType):
        return value.value
    text = str(value or SupplyType.UNKNOWN.value)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.upper()
