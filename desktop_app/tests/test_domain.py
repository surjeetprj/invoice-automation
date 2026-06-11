from __future__ import annotations

"""Regression tests for desktop parsing and validation helpers."""

import unittest

from desktop_app.domain.parsing import parse_date, parse_decimal
from desktop_app.domain.schemas import InvoiceData, LineItem, SupplyType, TaxDetail
from desktop_app.services.ai_parser import normalize_extracted_data, to_float
from desktop_app.ui.detail_page import cast_line_field
from desktop_app.domain.validation import validate_gstin, validate_invoice, validate_supply_type


class DomainHelperTests(unittest.TestCase):
    """Regression tests for desktop parsing and validation helpers."""

    def test_parse_decimal_handles_currency_tokens_and_commas(self) -> None:
        """Currency labels and comma grouping should not break numeric parsing."""
        self.assertEqual(parse_decimal("₹1,200"), 1200.0)
        self.assertEqual(parse_decimal("INR 5,310.50"), 5310.50)
        self.assertEqual(parse_decimal("Rs. 1,200"), 1200.0)
        self.assertEqual(parse_decimal("1,234.56"), 1234.56)
        self.assertIsNone(parse_decimal("", empty_as_none=True))

    def test_ai_parser_to_float_uses_shared_decimal_rules(self) -> None:
        """AI normalization should parse formatted currency through the shared helper."""
        self.assertEqual(to_float("₹1,200"), 1200.0)
        self.assertEqual(to_float("Rs. 1,200"), 1200.0)
        self.assertEqual(to_float("INR 5,310.50"), 5310.50)
        self.assertEqual(to_float(None), 0.0)

    def test_ai_normalization_derives_missing_total_tax_amount(self) -> None:
        """Component tax totals should fill total_tax_amount when AI omits it."""
        data = normalize_extracted_data({
            "total_taxable_amount": 21613.0,
            "total_igst": 3890.34,
            "total_cgst": 0.0,
            "total_sgst": 0.0,
            "total_cess": 0.0,
            "total_tax_amount": 0.0,
            "round_off": 0.0,
            "total_amount": 25503.34,
            "line_items": [],
        })
        self.assertEqual(data["total_tax_amount"], 3890.34)
        invoice = InvoiceData(vendor_name="SKE", invoice_number="SKEC2026042908", date="29-05-2026", **data)
        result = validate_invoice(invoice)
        self.assertFalse(any("Grand total mismatch" in error for error in result.errors))

    def test_validation_uses_component_tax_total_when_total_tax_amount_missing(self) -> None:
        """Validation should not fail grand total when only IGST aggregate is present."""
        invoice = InvoiceData(
            vendor_name="Relyon Softech Limited",
            invoice_number="RSL2026DI000215",
            date="08-05-2026",
            total_taxable_amount=2467.0,
            total_igst=444.06,
            total_tax_amount=0.0,
            round_off=-0.06,
            total_amount=2911.0,
            line_items=[
                LineItem(
                    description="Saral IncomeTax",
                    quantity=1.0,
                    rate=2467.0,
                    taxable_value=2467.0,
                    taxes=[TaxDetail(tax_type="IGST", tax_rate=18.0, taxable_amount=2467.0, tax_amount=444.06)],
                )
            ],
        )
        result = validate_invoice(invoice)
        self.assertFalse(any("Grand total mismatch" in error for error in result.errors))

    def test_line_item_casting_rejects_invalid_numeric_text(self) -> None:
        """Invalid numeric table values should raise instead of becoming zero."""
        with self.assertRaises(ValueError):
            cast_line_field("quantity", "abc")
        with self.assertRaises(ValueError):
            cast_line_field("sr_no", "1.2")
        self.assertEqual(cast_line_field("quantity", ""), 0.0)
        self.assertIsNone(cast_line_field("sr_no", ""))
        self.assertEqual(cast_line_field("rate", "123.45"), 123.45)

    def test_parse_date_accepts_common_invoice_formats(self) -> None:
        """Common invoice date formats should be normalized by one helper."""
        self.assertIsNotNone(parse_date("01-05-2026"))
        self.assertIsNotNone(parse_date("2026-05-01"))
        self.assertIsNone(parse_date("not a date"))

    def test_validate_gstin_detects_bad_length(self) -> None:
        """GSTIN validation should surface invalid lengths as errors."""
        errors: list[str] = []
        warnings: list[str] = []
        validate_gstin("09ABC", "Vendor", errors, warnings)
        self.assertTrue(errors)
        self.assertFalse(warnings)

    def test_validate_supply_type_checks_state_codes(self) -> None:
        """Supply type mismatches should be reported without changing data."""
        errors: list[str] = []
        warnings: list[str] = []
        data = InvoiceData(
            vendor_gstin="09AAOCS7654P3Z5",
            customer_gstin="27ABCDE1234F1Z5",
            supply_type=SupplyType.INTRA_STATE,
        )
        validate_supply_type(data, errors, warnings)
        self.assertTrue(any("differ" in warning for warning in warnings))

    def test_validate_invoice_reports_required_fields(self) -> None:
        """Empty invoices should not silently pass validation."""
        result = validate_invoice(InvoiceData(line_items=[LineItem(description="Service", taxable_value=100)]))
        self.assertFalse(result.is_valid)
        self.assertTrue(any("invoice_number" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
