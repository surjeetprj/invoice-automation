from __future__ import annotations

"""Persistence helpers for normalized invoice database rows."""

from typing import Any

from sqlalchemy.orm import Session

from ..domain.schemas import InvoiceData, LineItem, TaxDetail, ValidationIssue, ValidationResult
from .models import (
    Invoice,
    InvoiceExtraction,
    InvoiceLineItem,
    InvoiceLineTax,
    InvoiceTaxBreakup,
    InvoiceValidationIssue,
)

SCALAR_FIELDS = (
    "invoice_number", "date", "due_date", "challan_no", "challan_date", "e_way_bill_no",
    "supply_type", "reverse_charge", "irn", "ack_number", "ack_date", "qr_code_data",
    "vendor_name", "vendor_address", "vendor_gstin", "vendor_state_code", "vendor_pan",
    "vendor_msme_no", "vendor_contact", "customer_name", "customer_address",
    "customer_gstin", "customer_state_code", "customer_pan", "customer_phone",
    "place_of_supply", "shipping_name", "shipping_address", "shipping_gstin",
    "transport_name", "transport_id", "vehicle_number", "total_taxable_amount",
    "total_cgst", "total_sgst", "total_igst", "total_cess", "total_tax_amount",
    "round_off", "total_amount", "amount_in_words", "bank_name", "account_no",
    "ifsc", "branch", "confidence_score",
)


def persist_extraction(
    db: Session,
    invoice: Invoice,
    data: InvoiceData,
    validation: ValidationResult,
    raw_markdown: str | None,
) -> None:
    """Replace normalized extraction and validation rows for an invoice."""
    invoice.extraction = None
    invoice.validation_issues.clear()
    db.flush()
    invoice.extraction = build_extraction(data, raw_markdown)
    invoice.validation_issues = [
        InvoiceValidationIssue(severity=issue.severity, message=issue.message, field=issue.field)
        for issue in validation.issues
    ]
    apply_invoice_summary(invoice, data)
    db.flush()


def build_extraction(data: InvoiceData, raw_markdown: str | None) -> InvoiceExtraction:
    """Create ORM extraction rows from an InvoiceData object."""
    payload = data.model_dump(mode="json")
    extraction = InvoiceExtraction(raw_markdown=raw_markdown)
    for field in SCALAR_FIELDS:
        if field == "supply_type":
            value = data.supply_type.value if data.supply_type else None
        else:
            value = payload.get(field)
        setattr(extraction, field, value)
    extraction.line_items = [
        build_line_item(item, position)
        for position, item in enumerate(data.line_items, start=1)
    ]
    extraction.tax_breakups = [build_tax_breakup(tax) for tax in data.tax_breakup]
    return extraction


def build_line_item(item: LineItem, position: int) -> InvoiceLineItem:
    """Create one ORM line item and its tax rows."""
    row = InvoiceLineItem(
        position=position,
        sr_no=item.sr_no,
        description=item.description,
        hsn_sac=item.hsn_sac,
        quantity=item.quantity,
        unit=item.unit,
        rate=item.rate,
        discount=item.discount,
        taxable_value=item.taxable_value,
        cess_amount=item.cess_amount,
        total=item.total,
    )
    row.taxes = [
        InvoiceLineTax(
            tax_type=tax.tax_type,
            tax_rate=tax.tax_rate,
            taxable_amount=tax.taxable_amount,
            tax_amount=tax.tax_amount,
        )
        for tax in item.taxes
    ]
    return row


def build_tax_breakup(tax: TaxDetail) -> InvoiceTaxBreakup:
    """Create one invoice-level tax breakup row."""
    return InvoiceTaxBreakup(
        tax_type=tax.tax_type,
        tax_rate=tax.tax_rate,
        taxable_amount=tax.taxable_amount,
        tax_amount=tax.tax_amount,
    )


def apply_invoice_summary(invoice: Invoice, data: InvoiceData) -> None:
    """Copy high-value extracted fields onto the invoice summary row."""
    invoice.invoice_number_extracted = data.invoice_number
    invoice.invoice_date_extracted = data.date
    invoice.total_amount_extracted = data.total_amount
    invoice.vendor_gstin = data.vendor_gstin
    invoice.supply_type = data.supply_type.value if data.supply_type else None
    invoice.confidence_score = data.confidence_score


def invoice_data_from_invoice(invoice: Invoice) -> InvoiceData | None:
    """Rebuild InvoiceData from normalized rows."""
    extraction = invoice.extraction
    if extraction is None:
        return None
    payload: dict[str, Any] = {field: getattr(extraction, field) for field in SCALAR_FIELDS}
    payload["line_items"] = [line_item_from_row(row).model_dump(mode="json") for row in extraction.line_items]
    payload["tax_breakup"] = [tax_detail_from_row(row).model_dump(mode="json") for row in extraction.tax_breakups]
    return InvoiceData(**payload)


def line_item_from_row(row: InvoiceLineItem) -> LineItem:
    """Build a LineItem model from an ORM row."""
    return LineItem(
        sr_no=row.sr_no,
        description=row.description,
        hsn_sac=row.hsn_sac,
        quantity=row.quantity,
        unit=row.unit,
        rate=row.rate,
        discount=row.discount,
        taxable_value=row.taxable_value,
        taxes=[tax_detail_from_row(tax) for tax in row.taxes],
        cess_amount=row.cess_amount,
        total=row.total,
    )


def tax_detail_from_row(row: InvoiceLineTax | InvoiceTaxBreakup) -> TaxDetail:
    """Build a TaxDetail model from a line or invoice-level tax row."""
    return TaxDetail(
        tax_type=row.tax_type,
        tax_rate=row.tax_rate,
        taxable_amount=row.taxable_amount,
        tax_amount=row.tax_amount,
    )


def validation_from_invoice(invoice: Invoice) -> ValidationResult:
    """Rebuild ValidationResult from normalized validation issue rows."""
    errors = [issue.message for issue in invoice.validation_issues if issue.severity == "error"]
    warnings = [issue.message for issue in invoice.validation_issues if issue.severity == "warning"]
    issues = [
        ValidationIssue(severity=issue.severity, message=issue.message, field=issue.field)
        for issue in invoice.validation_issues
    ]
    return ValidationResult(is_valid=not errors, errors=errors, warnings=warnings, issues=issues)


def raw_markdown_from_invoice(invoice: Invoice) -> str | None:
    """Return raw extraction text for an invoice."""
    return invoice.extraction.raw_markdown if invoice.extraction else None
