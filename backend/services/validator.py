"""
Invoice Validator — Production-grade business-rule validation engine
for Indian GST Tax Invoices.

Supports:
- GSTIN format + state code cross-validation
- Inter-state vs intra-state tax consistency (IGST vs CGST+SGST)
- CGST = SGST rate pairing
- Valid GST rate slab enforcement
- HSN/SAC code format validation
- Discount math checks
- Round-off range validation
- E-way bill threshold warning
- Per-line-item and invoice-level math reconciliation
"""
import logging
import re
from datetime import datetime

from config import (
    EWAY_BILL_THRESHOLD,
    MATH_TOLERANCE,
    STATE_CODES,
    VALID_GST_RATES,
)
from schemas import InvoiceData, ValidationResult, FieldConfidence, ValidationIssue


logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# GSTIN format regex (15 characters)
# Format: 2-digit state code + 10-char PAN + 1 entity + 1 default (Z) + 1 check digit
# Example: 06AAGCA0983P1Z9
#   06      = State code (Haryana)
#   AAGCA0983P = PAN number
#   1       = Entity number
#   Z       = Default character
#   9       = Check digit
GSTIN_PATTERN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z][A-Z][A-Z0-9]$", re.IGNORECASE)

# HSN: 4, 6, or 8 digits
HSN_PATTERN = re.compile(r"^\d{4}(\d{2})?(\d{2})?$")

# SAC: 6 digits starting with 99
SAC_PATTERN = re.compile(r"^99\d{4}$")


# ──────────────────────────────────────────────
# Date validation helper
# ──────────────────────────────────────────────
_DATE_FORMATS = [
    "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y",
    "%d.%m.%Y", "%Y.%m.%d",
    "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y",
    "%b %d %Y", "%B %d %Y",
    "%d-%b-%Y", "%d-%B-%Y",
]


def _parse_date(date_str: str) -> datetime | None:
    """Try multiple formats to parse a date string."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def _validate_gstin(gstin: str, label: str, errors: list[str], warnings: list[str]):
    """Validate GSTIN format and add errors/warnings."""
    if not gstin:
        return
    cleaned = gstin.strip().upper()
    if len(cleaned) != 15:
        errors.append(f"{label} GSTIN '{gstin}' is not 15 characters (got {len(cleaned)})")
    elif not GSTIN_PATTERN.match(cleaned):
        warnings.append(f"{label} GSTIN '{gstin}' does not match standard format")
    else:
        # Validate state code
        state_code = cleaned[:2]
        if state_code not in STATE_CODES:
            warnings.append(f"{label} GSTIN state code '{state_code}' is not a recognized Indian state code")


def _validate_hsn_sac(code: str | None, line_idx: int, warnings: list[str]):
    """Validate HSN or SAC code format."""
    if not code:
        return
    cleaned = code.strip()
    if not cleaned:
        return

    if cleaned.startswith("99"):
        # SAC code
        if not SAC_PATTERN.match(cleaned):
            warnings.append(f"Line {line_idx}: SAC code '{cleaned}' should be 6 digits starting with 99")
    else:
        # HSN code
        if not HSN_PATTERN.match(cleaned):
            warnings.append(f"Line {line_idx}: HSN code '{cleaned}' should be 4, 6, or 8 digits")


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def validate_invoice(data: InvoiceData) -> ValidationResult:
    """
    Validate extracted invoice data against Indian GST business rules.

    Rules:
        1. Required fields: invoice_number, date, vendor_name, ≥1 line item.
        2. GSTIN format + state code validation for vendor and customer.
        3. Inter-state vs intra-state tax consistency.
        4. CGST = SGST rate pairing.
        5. Valid GST rate slab enforcement.
        6. HSN/SAC code format.
        7. Per-line-item math: (qty × rate - discount) ≈ taxable_value.
        8. Invoice-level math reconciliation.
        9. Date check: parseable and not in the future.
        10. Round-off within ±₹1.
        11. E-way bill threshold warning.
        12. Reverse charge presence check.
    """
    errors: list[str] = []
    warnings: list[str] = []
    field_confidences: list[FieldConfidence] = []

    # ── 1. Required fields ────────────────────
    if not data.invoice_number:
        errors.append("Missing required field: invoice_number")
        field_confidences.append(FieldConfidence(
            field_name="invoice_number", confidence=0.0, needs_review=True
        ))
    if not data.date:
        errors.append("Missing required field: date")
    if not data.vendor_name:
        errors.append("Missing required field: vendor_name")
    if not data.line_items:
        errors.append("No line items found in the invoice")

    # ── 2. GSTIN validation ───────────────────
    _validate_gstin(data.vendor_gstin, "Vendor", errors, warnings)
    _validate_gstin(data.customer_gstin, "Customer", errors, warnings)

    # ── 3. Date validation ────────────────────
    if data.date:
        parsed_dt = _parse_date(data.date)
        if parsed_dt is None:
            errors.append(f"Date '{data.date}' is not in a recognized format")
        elif parsed_dt > datetime.now():
            errors.append(f"Date '{data.date}' is in the future")

    # ── 4. Supply type & tax consistency ──────
    supply_type = data.supply_type.value if data.supply_type else "UNKNOWN"

    if supply_type == "INTRA_STATE":
        # Must have CGST+SGST, should NOT have IGST
        if data.total_igst > 0 and data.total_cgst == 0 and data.total_sgst == 0:
            errors.append(
                "Supply type is INTRA_STATE but only IGST is present. "
                "Intra-state invoices must use CGST + SGST."
            )
        if data.total_cgst > 0 and data.total_sgst == 0:
            warnings.append("CGST is present but SGST is zero — they should be equal")
        if data.total_sgst > 0 and data.total_cgst == 0:
            warnings.append("SGST is present but CGST is zero — they should be equal")
    elif supply_type == "INTER_STATE":
        # Must have IGST, should NOT have CGST+SGST
        if data.total_cgst > 0 and data.total_igst == 0:
            errors.append(
                "Supply type is INTER_STATE but CGST+SGST is present. "
                "Inter-state invoices must use IGST."
            )

    # ── 5. GSTIN state code cross-validation ──
    if data.vendor_gstin and data.customer_gstin:
        vendor_state = data.vendor_gstin[:2] if len(data.vendor_gstin) >= 2 else ""
        customer_state = data.customer_gstin[:2] if len(data.customer_gstin) >= 2 else ""

        if vendor_state and customer_state:
            same_state = (vendor_state == customer_state)

            if same_state and supply_type == "INTER_STATE":
                warnings.append(
                    f"Vendor and customer are in same state ({vendor_state}) "
                    f"but supply_type is INTER_STATE"
                )
            elif not same_state and supply_type == "INTRA_STATE":
                warnings.append(
                    f"Vendor ({vendor_state}) and customer ({customer_state}) are in different states "
                    f"but supply_type is INTRA_STATE"
                )

    # ── 6. Per-line-item validation ───────────
    computed_taxable_sum = 0.0
    computed_tax_sum = 0.0
    computed_cgst = 0.0
    computed_sgst = 0.0
    computed_igst = 0.0
    computed_cess = 0.0

    cgst_rates = set()
    sgst_rates = set()

    for idx, item in enumerate(data.line_items, start=1):
        computed_taxable_sum += item.taxable_value

        # Discount math: taxable_value ≈ (qty × rate) - discount
        if item.quantity > 0 and item.rate > 0:
            expected_taxable = (item.quantity * item.rate) - item.discount
            if abs(item.taxable_value - expected_taxable) > MATH_TOLERANCE:
                warnings.append(
                    f"Line {idx}: taxable_value (₹{item.taxable_value:.2f}) != "
                    f"qty ({item.quantity}) × rate (₹{item.rate:.2f}) "
                    f"- discount (₹{item.discount:.2f}) = ₹{expected_taxable:.2f}"
                )

        # HSN/SAC validation
        _validate_hsn_sac(item.hsn_sac, idx, warnings)

        # Per-item tax validation
        line_cgst_rate = 0.0
        line_sgst_rate = 0.0

        for tax in item.taxes:
            computed_tax_sum += tax.tax_amount
            tax_type_upper = tax.tax_type.upper()

            if tax_type_upper == "CGST":
                computed_cgst += tax.tax_amount
                line_cgst_rate = tax.tax_rate
                cgst_rates.add(tax.tax_rate)
            elif tax_type_upper == "SGST":
                computed_sgst += tax.tax_amount
                line_sgst_rate = tax.tax_rate
                sgst_rates.add(tax.tax_rate)
            elif tax_type_upper == "IGST":
                computed_igst += tax.tax_amount
            elif tax_type_upper == "CESS":
                computed_cess += tax.tax_amount

            # Valid GST rate slab check
            if tax_type_upper in ("CGST", "SGST", "IGST") and tax.tax_rate > 0:
                effective_rate = tax.tax_rate
                if tax_type_upper in ("CGST", "SGST"):
                    effective_rate = tax.tax_rate * 2  # CGST+SGST combined
                if effective_rate not in VALID_GST_RATES:
                    warnings.append(
                        f"Line {idx}: {tax.tax_type} rate {tax.tax_rate}% "
                        f"(effective {effective_rate}%) is not a standard GST slab "
                        f"({', '.join(str(r) + '%' for r in sorted(VALID_GST_RATES))})"
                    )

            # Tax amount math: taxable_value × (rate / 100) ≈ tax_amount
            if tax.tax_rate > 0 and item.taxable_value > 0:
                expected_tax = item.taxable_value * (tax.tax_rate / 100.0)
                if abs(tax.tax_amount - expected_tax) > MATH_TOLERANCE:
                    warnings.append(
                        f"Line {idx}: {tax.tax_type} math mismatch — "
                        f"expected ₹{expected_tax:.2f} "
                        f"(₹{item.taxable_value} × {tax.tax_rate}%), "
                        f"got ₹{tax.tax_amount:.2f}"
                    )

        # CGST = SGST rate check (must be equal)
        if line_cgst_rate > 0 and line_sgst_rate > 0:
            if abs(line_cgst_rate - line_sgst_rate) > 0.01:
                errors.append(
                    f"Line {idx}: CGST rate ({line_cgst_rate}%) ≠ SGST rate ({line_sgst_rate}%). "
                    f"CGST and SGST rates must always be equal."
                )

        # CESS handling
        computed_cess += item.cess_amount

    # ── 7. Invoice-level math ─────────────────
    if data.line_items:
        # Taxable amount check
        if data.total_taxable_amount > 0:
            if abs(computed_taxable_sum - data.total_taxable_amount) > MATH_TOLERANCE:
                warnings.append(
                    f"Taxable amount mismatch: sum of line items = ₹{computed_taxable_sum:.2f}, "
                    f"stated total_taxable_amount = ₹{data.total_taxable_amount:.2f}"
                )

        # Tax total check
        if data.total_tax_amount > 0:
            if abs(computed_tax_sum - data.total_tax_amount) > MATH_TOLERANCE:
                warnings.append(
                    f"Tax amount mismatch: sum of line taxes = ₹{computed_tax_sum:.2f}, "
                    f"stated total_tax_amount = ₹{data.total_tax_amount:.2f}"
                )

        # Per-tax-type totals
        if data.total_cgst > 0 and abs(computed_cgst - data.total_cgst) > MATH_TOLERANCE:
            warnings.append(
                f"CGST mismatch: computed ₹{computed_cgst:.2f} vs stated ₹{data.total_cgst:.2f}"
            )
        if data.total_sgst > 0 and abs(computed_sgst - data.total_sgst) > MATH_TOLERANCE:
            warnings.append(
                f"SGST mismatch: computed ₹{computed_sgst:.2f} vs stated ₹{data.total_sgst:.2f}"
            )
        if data.total_igst > 0 and abs(computed_igst - data.total_igst) > MATH_TOLERANCE:
            warnings.append(
                f"IGST mismatch: computed ₹{computed_igst:.2f} vs stated ₹{data.total_igst:.2f}"
            )

        # Grand total check: total = taxable + tax + round_off
        if data.total_amount > 0:
            expected_grand = data.total_taxable_amount + data.total_tax_amount + data.round_off
            if abs(data.total_amount - expected_grand) > MATH_TOLERANCE:
                errors.append(
                    f"Grand total mismatch: total_amount (₹{data.total_amount:.2f}) != "
                    f"taxable (₹{data.total_taxable_amount:.2f}) + "
                    f"tax (₹{data.total_tax_amount:.2f}) + "
                    f"round_off (₹{data.round_off:.2f}) = ₹{expected_grand:.2f}"
                )

    # ── 8. Round-off validation ───────────────
    if abs(data.round_off) > 1.0:
        warnings.append(
            f"Round-off amount ₹{data.round_off:.2f} exceeds ±₹1.00 — verify manually"
        )

    # ── 9. E-way bill threshold ───────────────
    if data.total_amount > EWAY_BILL_THRESHOLD and not data.e_way_bill_no:
        warnings.append(
            f"Invoice value ₹{data.total_amount:.2f} exceeds ₹{EWAY_BILL_THRESHOLD:.0f} "
            f"but no E-Way Bill number found. E-Way Bill is required for high-value shipments."
        )

    # ── 10. Reverse charge check ──────────────
    if not data.reverse_charge:
        warnings.append("Reverse Charge field not found — should be 'Y' or 'N' on GST invoices")

    # ── 11. Place of supply check ─────────────
    if not data.place_of_supply:
        warnings.append("Place of Supply not found — required for GST compliance")

    # ── 12. Confidence-based field flags ──────
    # (confidence is now calculated separately via calculate_confidence_score)

    is_valid = len(errors) == 0

    logger.info(
        "Validation %s — %d error(s), %d warning(s)",
        "PASSED" if is_valid else "FAILED",
        len(errors),
        len(warnings),
    )
    for e in errors:
        logger.warning("  ✗ %s", e)
    for w in warnings:
        logger.info("  ⚠ %s", w)

    # Construct issues list for UI dashboard
    issues = []
    for err in errors:
        field = "General"
        err_lower = err.lower()
        if "gstin" in err_lower:
            field = "GSTIN"
        elif "date" in err_lower:
            field = "Date"
        elif "invoice_number" in err_lower or "invoice #" in err_lower:
            field = "Invoice Number"
        elif any(k in err_lower for k in ["amount", "total", "taxable", "cgst", "sgst", "igst", "cess", "math"]):
            field = "Calculation"
        elif "line" in err_lower or "item" in err_lower:
            field = "Line Items"
            
        issues.append(ValidationIssue(severity="error", message=err, field=field))
        
    for warn in warnings:
        field = "General"
        warn_lower = warn.lower()
        if "gstin" in warn_lower:
            field = "GSTIN"
        elif "date" in warn_lower:
            field = "Date"
        elif "reverse charge" in warn_lower:
            field = "Reverse Charge"
        elif "place of supply" in warn_lower:
            field = "Place of Supply"
        elif "e-way bill" in warn_lower:
            field = "E-Way Bill"
        elif "hsn" in warn_lower or "sac" in warn_lower:
            field = "HSN/SAC Code"
        elif any(k in warn_lower for k in ["amount", "total", "taxable", "cgst", "sgst", "igst", "cess", "math"]):
            field = "Calculation"
        elif "line" in warn_lower or "item" in warn_lower:
            field = "Line Items"
            
        issues.append(ValidationIssue(severity="warning", message=warn, field=field))

    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        warnings=warnings,
        field_confidences=field_confidences,
        issues=issues,
    )



# ──────────────────────────────────────────────
# Calculated Confidence Score
# ──────────────────────────────────────────────
def calculate_confidence_score(
    data: InvoiceData,
    validation: ValidationResult,
) -> float:
    """
    Calculate an evidence-based confidence score (0.0–1.0) from actual
    extraction results — NOT the LLM's self-reported guess.

    Scoring breakdown:
        ┌─────────────────────────────┬────────┬──────────────────────────────┐
        │ Factor                      │ Weight │ Logic                        │
        ├─────────────────────────────┼────────┼──────────────────────────────┤
        │ Critical fields present     │  30%   │ 6 key fields checked         │
        │ Line items quality          │  20%   │ Items with desc + amount > 0 │
        │ Math reconciliation         │  20%   │ Taxable, tax, grand totals   │
        │ Tax info present            │  15%   │ Has tax breakup / per-item   │
        │ Validation errors           │  15%   │ 0 errors = full, -5% each    │
        └─────────────────────────────┴────────┴──────────────────────────────┘

    Args:
        data: The extracted InvoiceData.
        validation: The ValidationResult from validate_invoice().

    Returns:
        Confidence score between 0.0 and 1.0.
    """
    score = 0.0

    # ── 1. Critical fields present (30%) ──────
    critical_fields = {
        "invoice_number": bool(data.invoice_number),
        "date": bool(data.date),
        "vendor_name": bool(data.vendor_name),
        "vendor_gstin": bool(data.vendor_gstin) and len(data.vendor_gstin or "") == 15,
        "customer_name": bool(data.customer_name),
        "customer_gstin": bool(data.customer_gstin) and len(data.customer_gstin or "") == 15,
    }
    fields_present = sum(critical_fields.values())
    fields_score = 0.30 * (fields_present / len(critical_fields))
    score += fields_score

    # ── 2. Line items quality (20%) ───────────
    items_score = 0.0
    if data.line_items:
        good_items = sum(
            1 for item in data.line_items
            if item.description and item.description != "Product/Service"
            and item.taxable_value > 0
        )
        item_quality = min(good_items / max(len(data.line_items), 1), 1.0)
        items_score = 0.20 * item_quality
    score += items_score

    # ── 3. Math reconciliation (20%) ──────────
    math_checks_passed = 0
    math_checks_total = 0

    # Check: sum of line taxable values ≈ total_taxable_amount
    if data.line_items and data.total_taxable_amount > 0:
        math_checks_total += 1
        computed_taxable = sum(item.taxable_value for item in data.line_items)
        if abs(computed_taxable - data.total_taxable_amount) <= MATH_TOLERANCE:
            math_checks_passed += 1

    # Check: sum of line taxes ≈ total_tax_amount
    if data.line_items and data.total_tax_amount > 0:
        math_checks_total += 1
        computed_tax = sum(
            tax.tax_amount for item in data.line_items for tax in item.taxes
        )
        if abs(computed_tax - data.total_tax_amount) <= MATH_TOLERANCE:
            math_checks_passed += 1

    # Check: grand total ≈ taxable + tax + round_off
    if data.total_amount > 0 and data.total_taxable_amount > 0:
        math_checks_total += 1
        expected = data.total_taxable_amount + data.total_tax_amount + data.round_off
        if abs(data.total_amount - expected) <= MATH_TOLERANCE:
            math_checks_passed += 1

    math_score = 0.0
    if math_checks_total > 0:
        math_score = 0.20 * (math_checks_passed / math_checks_total)
    elif data.total_amount > 0:
        math_score = 0.05
    score += math_score

    # ── 4. Tax info present (15%) ─────────────
    has_invoice_tax = bool(data.tax_breakup)
    has_item_taxes = any(item.taxes for item in data.line_items)
    has_tax_totals = (
        data.total_cgst > 0 or data.total_sgst > 0 or
        data.total_igst > 0 or data.total_tax_amount > 0
    )

    tax_signals = sum([has_invoice_tax, has_item_taxes, has_tax_totals])
    tax_score = 0.15 * (tax_signals / 3)
    score += tax_score

    # ── 5. No validation errors (15%) ─────────
    error_count = len(validation.errors)
    if error_count == 0:
        errors_score = 0.15
    else:
        penalty = min(error_count * 0.05, 0.15)
        errors_score = max(0.15 - penalty, 0.0)
    score += errors_score

    # Clamp to [0.0, 1.0] and round
    final_score = round(min(max(score, 0.0), 1.0), 2)

    # Log per-factor breakdown: earned / max weight
    missing_fields = [k for k, v in critical_fields.items() if not v]
    missing_str = f" (missing: {', '.join(missing_fields)})" if missing_fields else ""

    logger.info(
        "Confidence score: %.2f — "
        "fields=%.0f/30%s | "
        "items=%.0f/20 (%d/%d good) | "
        "math=%.0f/20 (%d/%d checks) | "
        "tax=%.0f/15 (%d/3 signals) | "
        "errors=%.0f/15 (%d errors)",
        final_score,
        fields_score * 100, missing_str,
        items_score * 100, sum(1 for i in data.line_items if i.description and i.taxable_value > 0) if data.line_items else 0, len(data.line_items),
        math_score * 100, math_checks_passed, math_checks_total,
        tax_score * 100, tax_signals,
        errors_score * 100, error_count,
    )

    return final_score

