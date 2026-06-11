from __future__ import annotations

"""GST invoice validation and confidence scoring for desktop processing."""

import re

from ..config import EWAY_BILL_THRESHOLD, MATH_TOLERANCE, STATE_CODES, VALID_GST_RATES
from .parsing import parse_date
from .schemas import FieldConfidence, InvoiceData, ValidationIssue, ValidationResult


GSTIN_PATTERN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z][A-Z][A-Z0-9]$", re.IGNORECASE)
HSN_PATTERN = re.compile(r"^\d{4}(\d{2})?(\d{2})?$")
SAC_PATTERN = re.compile(r"^99\d{4}$")


def validate_invoice(data: InvoiceData) -> ValidationResult:
    """Validate extracted invoice data against GST and arithmetic rules."""
    errors: list[str] = []
    warnings: list[str] = []
    field_confidences: list[FieldConfidence] = []

    if not data.invoice_number:
        errors.append("Missing required field: invoice_number")
        field_confidences.append(FieldConfidence(field_name="invoice_number", confidence=0.0, needs_review=True))
    if not data.date:
        errors.append("Missing required field: date")
    elif not parse_date(data.date):
        errors.append(f"Date '{data.date}' is not in a recognized format")
    if not data.vendor_name:
        errors.append("Missing required field: vendor_name")
    if not data.line_items:
        errors.append("No line items found in the invoice")

    validate_gstin(data.vendor_gstin, "Vendor", errors, warnings)
    validate_gstin(data.customer_gstin, "Customer", errors, warnings)
    validate_supply_type(data, errors, warnings)

    computed_taxable = 0.0
    computed_tax = 0.0
    computed_cgst = 0.0
    computed_sgst = 0.0
    computed_igst = 0.0
    for index, item in enumerate(data.line_items, start=1):
        computed_taxable += item.taxable_value
        if item.quantity > 0 and item.rate > 0:
            expected = (item.quantity * item.rate) - item.discount
            if abs(item.taxable_value - expected) > MATH_TOLERANCE:
                warnings.append(f"Line {index}: taxable value does not match quantity x rate - discount")
        validate_hsn_sac(item.hsn_sac, index, warnings)
        for tax in item.taxes:
            computed_tax += tax.tax_amount
            tax_type = tax.tax_type.upper()
            if tax_type == "CGST":
                computed_cgst += tax.tax_amount
            elif tax_type == "SGST":
                computed_sgst += tax.tax_amount
            elif tax_type == "IGST":
                computed_igst += tax.tax_amount
            if tax_type in {"CGST", "SGST", "IGST"} and tax.tax_rate > 0:
                effective = tax.tax_rate * 2 if tax_type in {"CGST", "SGST"} else tax.tax_rate
                if effective not in VALID_GST_RATES:
                    warnings.append(f"Line {index}: GST rate {effective}% is not a standard slab")

    if data.line_items and data.total_taxable_amount > 0 and abs(computed_taxable - data.total_taxable_amount) > MATH_TOLERANCE:
        warnings.append("Taxable amount mismatch between line items and invoice total")
    effective_tax_total = effective_total_tax_amount(data, computed_tax)
    if effective_tax_total > 0 and computed_tax > 0 and abs(computed_tax - effective_tax_total) > MATH_TOLERANCE:
        warnings.append("Tax amount mismatch between line taxes and invoice total")
    if data.total_cgst > 0 and abs(computed_cgst - data.total_cgst) > MATH_TOLERANCE:
        warnings.append("CGST total mismatch")
    if data.total_sgst > 0 and abs(computed_sgst - data.total_sgst) > MATH_TOLERANCE:
        warnings.append("SGST total mismatch")
    if data.total_igst > 0 and abs(computed_igst - data.total_igst) > MATH_TOLERANCE:
        warnings.append("IGST total mismatch")
    if data.total_amount > 0:
        expected_total = data.total_taxable_amount + effective_tax_total + data.round_off
        if abs(data.total_amount - expected_total) > MATH_TOLERANCE:
            errors.append("Grand total mismatch: taxable + tax + round off does not equal total amount")
    if abs(data.round_off) > 1.0:
        warnings.append("Round-off exceeds +/-1.00; verify manually")
    if data.total_amount > EWAY_BILL_THRESHOLD and not data.e_way_bill_no:
        warnings.append("Invoice value exceeds E-Way Bill threshold but no E-Way Bill number was found")
    if not data.reverse_charge:
        warnings.append("Reverse Charge field not found")
    if not data.place_of_supply:
        warnings.append("Place of Supply not found")

    issues = [ValidationIssue(severity="error", message=msg, field=infer_issue_field(msg)) for msg in errors]
    issues += [ValidationIssue(severity="warning", message=msg, field=infer_issue_field(msg)) for msg in warnings]
    return ValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
        field_confidences=field_confidences,
        issues=issues,
    )


def effective_total_tax_amount(data: InvoiceData, computed_tax: float = 0.0) -> float:
    """Return the best available aggregate tax amount for invoice total checks."""
    if data.total_tax_amount > 0:
        return data.total_tax_amount
    component_total = data.total_cgst + data.total_sgst + data.total_igst + data.total_cess
    if component_total > 0:
        return component_total
    return computed_tax


def calculate_confidence_score(data: InvoiceData, validation: ValidationResult) -> float:
    """Calculate an evidence-based extraction confidence score."""
    score = 0.0
    critical = [
        bool(data.invoice_number), bool(data.date), bool(data.vendor_name),
        bool(data.vendor_gstin and len(data.vendor_gstin) == 15),
        bool(data.customer_name), bool(data.customer_gstin and len(data.customer_gstin) == 15),
    ]
    score += 0.30 * (sum(critical) / len(critical))
    if data.line_items:
        good = sum(1 for item in data.line_items if item.description and item.taxable_value > 0)
        score += 0.20 * (good / len(data.line_items))
    tax_signals = sum([
        bool(data.tax_breakup),
        any(item.taxes for item in data.line_items),
        data.total_tax_amount > 0 or data.total_cgst > 0 or data.total_igst > 0,
    ])
    score += 0.15 * (tax_signals / 3)
    score += 0.20 if data.total_amount > 0 else 0.0
    score += max(0.15 - min(len(validation.errors) * 0.05, 0.15), 0.0)
    return round(min(max(score, 0.0), 1.0), 2)


def validate_gstin(gstin: str | None, label: str, errors: list[str], warnings: list[str]) -> None:
    """Validate GSTIN length, pattern, and state code."""
    if not gstin:
        return
    cleaned = gstin.strip().upper()
    if len(cleaned) != 15:
        errors.append(f"{label} GSTIN '{gstin}' is not 15 characters")
    elif not GSTIN_PATTERN.match(cleaned):
        warnings.append(f"{label} GSTIN '{gstin}' does not match standard format")
    elif cleaned[:2] not in STATE_CODES:
        warnings.append(f"{label} GSTIN state code '{cleaned[:2]}' is not recognized")


def validate_hsn_sac(code: str | None, index: int, warnings: list[str]) -> None:
    """Validate HSN or SAC code shape for a line item."""
    if not code:
        return
    cleaned = code.strip()
    if cleaned.startswith("99") and not SAC_PATTERN.match(cleaned):
        warnings.append(f"Line {index}: SAC code should be 6 digits starting with 99")
    elif not cleaned.startswith("99") and not HSN_PATTERN.match(cleaned):
        warnings.append(f"Line {index}: HSN code should be 4, 6, or 8 digits")


def validate_supply_type(data: InvoiceData, errors: list[str], warnings: list[str]) -> None:
    """Validate GST supply type against tax totals and GSTIN state codes."""
    supply_type = data.supply_type.value if data.supply_type else "UNKNOWN"
    if supply_type == "INTRA_STATE" and data.total_igst > 0 and data.total_cgst == 0 and data.total_sgst == 0:
        errors.append("Supply type is INTRA_STATE but only IGST is present")
    if supply_type == "INTER_STATE" and data.total_cgst > 0 and data.total_igst == 0:
        errors.append("Supply type is INTER_STATE but CGST/SGST is present")
    if data.vendor_gstin and data.customer_gstin and len(data.vendor_gstin) >= 2 and len(data.customer_gstin) >= 2:
        same_state = data.vendor_gstin[:2] == data.customer_gstin[:2]
        if same_state and supply_type == "INTER_STATE":
            warnings.append("Vendor and customer GSTIN state codes match but supply type is INTER_STATE")
        if not same_state and supply_type == "INTRA_STATE":
            warnings.append("Vendor and customer GSTIN state codes differ but supply type is INTRA_STATE")


def infer_issue_field(message: str) -> str:
    """Map a validation message to a UI issue field group."""
    lower = message.lower()
    if "gstin" in lower:
        return "GSTIN"
    if "date" in lower:
        return "Date"
    if "invoice" in lower and "number" in lower:
        return "Invoice Number"
    if any(key in lower for key in ["amount", "total", "tax", "cgst", "sgst", "igst", "cess"]):
        return "Calculation"
    if "line" in lower or "item" in lower:
        return "Line Items"
    return "General"


__all__ = [
    "calculate_confidence_score",
    "infer_issue_field",
    "validate_gstin",
    "validate_hsn_sac",
    "validate_invoice",
    "validate_supply_type",
]
