from __future__ import annotations

"""Regression tests for desktop parsing and validation helpers."""

import unittest

from desktop_app.domain.parsing import parse_date, parse_decimal
from desktop_app.domain.schemas import InvoiceData, LineItem, SupplyType
from desktop_app.services.ai_parser import to_float
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
