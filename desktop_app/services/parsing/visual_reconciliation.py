from __future__ import annotations

"""Visual invoice reconciliation for scanned PDFs and image uploads."""

from typing import Any

from ...config import CURRENCY_DECIMAL_PLACES, MATH_TOLERANCE
from .gst_normalization import to_float

VISUAL_DOCUMENT_KINDS = {"SCANNED_PDF", "IMAGE"}


def reconcile_visual_line_items(data: dict[str, Any], document_kind: str | None = None) -> None:
    """Replace unreliable visual line rows with one balanced purchase summary line."""
    if normalized_document_kind(document_kind) not in VISUAL_DOCUMENT_KINDS:
        return
    if not invoice_totals_are_consistent(data):
        return
    invoice_taxable = data.get("total_taxable_amount", 0.0)
    items = [item for item in data.get("line_items") or [] if isinstance(item, dict)]
    if invoice_taxable <= 0 or not items:
        return
    line_taxable_total = round(sum(to_float(item.get("taxable_value")) for item in items), CURRENCY_DECIMAL_PLACES)
    if abs(line_taxable_total - invoice_taxable) <= MATH_TOLERANCE:
        return

    best_item = best_line_item(items)
    data["line_items"] = [build_summary_line_item(data, best_item)]


def invoice_totals_are_consistent(data: dict[str, Any]) -> bool:
    """Return True when invoice-level subtotal, tax, round-off, and total reconcile."""
    taxable = data.get("total_taxable_amount", 0.0)
    total = data.get("total_amount", 0.0)
    if taxable <= 0 or total <= 0:
        return False
    tax_total = effective_invoice_tax_total(data)
    expected_total = round(taxable + tax_total + data.get("round_off", 0.0), CURRENCY_DECIMAL_PLACES)
    return abs(total - expected_total) <= MATH_TOLERANCE


def effective_invoice_tax_total(data: dict[str, Any]) -> float:
    """Return the invoice-level tax total from aggregate fields."""
    if data.get("total_tax_amount", 0.0) > 0:
        return data["total_tax_amount"]
    return data.get("total_cgst", 0.0) + data.get("total_sgst", 0.0) + data.get("total_igst", 0.0) + data.get("total_cess", 0.0)


def best_line_item(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the most useful existing line for description and HSN/SAC context."""
    return max(
        items,
        key=lambda item: (
            bool(str(item.get("description") or "").strip()),
            bool(str(item.get("hsn_sac") or "").strip()),
            len(str(item.get("description") or "")),
        ),
    )


def build_summary_line_item(data: dict[str, Any], source_item: dict[str, Any]) -> dict[str, Any]:
    """Build one line item whose taxable and tax values match invoice totals."""
    taxable = round(data.get("total_taxable_amount", 0.0), CURRENCY_DECIMAL_PLACES)
    taxes = build_summary_taxes(data, taxable, source_item)
    cess_amount = round(data.get("total_cess", 0.0), CURRENCY_DECIMAL_PLACES)
    tax_total = round(sum(tax["tax_amount"] for tax in taxes), CURRENCY_DECIMAL_PLACES)
    return {
        "sr_no": source_item.get("sr_no") or 1,
        "description": summary_description(source_item),
        "hsn_sac": source_item.get("hsn_sac"),
        "quantity": 1.0,
        "unit": source_item.get("unit"),
        "rate": taxable,
        "discount": 0.0,
        "taxable_value": taxable,
        "taxes": taxes,
        "cess_amount": cess_amount,
        "total": round(taxable + tax_total + cess_amount, CURRENCY_DECIMAL_PLACES),
    }


def build_summary_taxes(data: dict[str, Any], taxable: float, source_item: dict[str, Any]) -> list[dict[str, Any]]:
    """Create line tax rows from aggregate GST totals."""
    taxes: list[dict[str, Any]] = []
    for tax_type, amount_field in (("CGST", "total_cgst"), ("SGST", "total_sgst"), ("IGST", "total_igst")):
        tax_amount = round(data.get(amount_field, 0.0), CURRENCY_DECIMAL_PLACES)
        if tax_amount <= 0:
            continue
        taxes.append({
            "tax_type": tax_type,
            "tax_rate": summary_tax_rate(tax_type, taxable, tax_amount, source_item),
            "taxable_amount": taxable,
            "tax_amount": tax_amount,
        })
    return taxes


def summary_tax_rate(tax_type: str, taxable: float, tax_amount: float, source_item: dict[str, Any]) -> float:
    """Prefer an extracted tax rate, falling back to amount/taxable math."""
    for tax in source_item.get("taxes") or []:
        if not isinstance(tax, dict):
            continue
        if str(tax.get("tax_type") or "").upper() == tax_type and to_float(tax.get("tax_rate")) > 0:
            return round(to_float(tax.get("tax_rate")), CURRENCY_DECIMAL_PLACES)
    if taxable > 0:
        return round((tax_amount / taxable) * 100, CURRENCY_DECIMAL_PLACES)
    return 0.0


def summary_description(source_item: dict[str, Any]) -> str:
    """Return a stable summary description while preserving visible item text."""
    description = str(source_item.get("description") or "").strip()
    return description or "Purchase as per invoice"


def normalized_document_kind(document_kind: Any) -> str | None:
    """Normalize source kind values from enums or plain strings."""
    if document_kind is None:
        return None
    value = getattr(document_kind, "value", document_kind)
    return str(value).upper()
