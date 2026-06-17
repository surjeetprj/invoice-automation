from __future__ import annotations

"""Regression tests for the invoice detail review UI."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from desktop_app.ui.constants import FIELD_GROUPS, LINE_COLUMNS, REQUIRED_METADATA_FIELDS
from desktop_app.ui.detail_page import DetailPage


class DetailPageLayoutTests(unittest.TestCase):
    """Exercise the compact metadata review layout without showing a window."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        self.app.processEvents()

    def test_metadata_tab_contains_all_fields_and_line_items(self) -> None:
        """All existing metadata fields should remain in the Metadata tab."""
        page = DetailPage()
        try:
            expected_fields = {field for fields in FIELD_GROUPS.values() for field in fields}
            self.assertEqual(expected_fields, set(page.metadata.fields))
            self.assertIs(page.metadata.line_items, page.line_items)
            self.assertEqual([page.tabs.tabText(index) for index in range(page.tabs.count())], ["Metadata", "Validation", "Audit Logs"])
            self.assertEqual(page.splitter.widget(1).minimumWidth(), 360)
            labels = [action.text() for action in page.export_btn.menu().actions()]
            self.assertIn("Post Purchase Voucher to TallyPrime", labels)
            self.assertIn("Post Item-wise Purchase Voucher to TallyPrime", labels)
        finally:
            page.deleteLater()

    def test_required_fields_are_flagged_without_blocking_review_actions(self) -> None:
        """Required markers are informational and do not replace backend validation."""
        page = DetailPage()
        try:
            page.load_invoice(
                {
                    "id": 1,
                    "status": "Extracted",
                    "confidence_score": 0.5,
                    "extracted_data": {},
                    "validation": {"is_valid": True, "errors": [], "warnings": [], "issues": []},
                }
            )
            for field in REQUIRED_METADATA_FIELDS:
                self.assertEqual(page.metadata.fields[field].objectName(), "requiredMissing")
            self.assertTrue(page.approve_btn.isEnabled())
            self.assertIn("At least one line item", page.metadata.line_items_section.hint.text())
        finally:
            page.deleteLater()

    def test_embedded_line_items_are_included_in_corrections(self) -> None:
        """Moving line items into Metadata must preserve correction payloads."""
        page = DetailPage()
        try:
            page.load_invoice(
                {
                    "id": 2,
                    "status": "Extracted",
                    "confidence_score": 0.9,
                    "extracted_data": {
                        "invoice_number": "INV-1",
                        "date": "01-04-2026",
                        "vendor_name": "Vendor A",
                        "total_taxable_amount": 100.0,
                        "total_amount": 118.0,
                        "line_items": [
                            {
                                "sr_no": 1,
                                "description": "Old item",
                                "hsn_sac": "9983",
                                "quantity": 1.0,
                                "unit": "Nos",
                                "rate": 100.0,
                                "discount": 0.0,
                                "taxable_value": 100.0,
                                "taxes": [{"tax_type": "IGST", "tax_rate": 18.0, "taxable_amount": 100.0, "tax_amount": 18.0}],
                                "cess_amount": 0.0,
                                "total": 118.0,
                            }
                        ],
                    },
                    "validation": {"is_valid": True, "errors": [], "warnings": [], "issues": []},
                }
            )
            description_column = [name for name, _label in LINE_COLUMNS].index("description")
            self.assertEqual(page.line_items.table.rowCount(), 1)
            self.assertGreaterEqual(page.line_items.table.minimumHeight(), 170)
            self.assertEqual(page.line_items.table.item(0, description_column).text(), "Old item")
            page.line_items.table.item(0, description_column).setText("Updated item")
            corrections = page.build_corrections()
            self.assertEqual(corrections["line_items"][0]["description"], "Updated item")
            self.assertEqual(corrections["line_items"][0]["taxes"][0]["tax_type"], "IGST")
        finally:
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()
