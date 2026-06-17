from __future__ import annotations

"""Regression tests for the invoice detail review UI."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from desktop_app.ui.constants import FIELD_GROUPS, LINE_COLUMNS, REQUIRED_METADATA_FIELDS
from desktop_app.ui.detail_page import DetailPage
from desktop_app.ui.main_window import MainWindow


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
                                "item_name": "Old clean item",
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
            item_name_column = [name for name, _label in LINE_COLUMNS].index("item_name")
            self.assertEqual(page.line_items.table.rowCount(), 1)
            self.assertGreaterEqual(page.line_items.table.minimumHeight(), 170)
            self.assertEqual(page.line_items.table.item(0, item_name_column).text(), "Old clean item")
            self.assertEqual(page.line_items.table.item(0, description_column).text(), "Old item")
            page.line_items.table.item(0, item_name_column).setText("Updated clean item")
            page.line_items.table.item(0, description_column).setText("Updated item")
            corrections = page.build_corrections()
            self.assertEqual(corrections["line_items"][0]["item_name"], "Updated clean item")
            self.assertEqual(corrections["line_items"][0]["description"], "Updated item")
            self.assertEqual(corrections["line_items"][0]["taxes"][0]["tax_type"], "IGST")
        finally:
            page.deleteLater()

    def test_submit_corrections_sends_save_without_approval_decision(self) -> None:
        """The Submit Corrections action should save edits without approving."""

        class FakeWorkflow:
            def __init__(self) -> None:
                self.invoice_id = None
                self.payload = None

            def submit_review(self, invoice_id, payload):
                self.invoice_id = invoice_id
                self.payload = payload
                return {"id": invoice_id}

        class DummyWindow:
            def __init__(self) -> None:
                self.workflow = FakeWorkflow()
                self.loaded_invoice = None

            def reviewer_name(self):
                return "reviewer"

            def run_task(self, task, callback):
                result = task()
                callback(result)

            def load_saved_invoice(self, invoice):
                self.loaded_invoice = invoice

        window = DummyWindow()
        MainWindow.submit_corrections(window, 5, {"vendor_name": "Corrected Vendor"})

        self.assertEqual(window.workflow.invoice_id, 5)
        self.assertEqual(window.workflow.payload["decision"], "save_corrections")
        self.assertEqual(window.workflow.payload["reviewer"], "reviewer")
        self.assertEqual(window.workflow.payload["corrections"], {"vendor_name": "Corrected Vendor"})
        self.assertEqual(window.loaded_invoice, {"id": 5})

    def test_corrections_button_reenables_after_saved_invoice_is_edited_again(self) -> None:
        """After a saved correction reloads, another edit should enable submit again."""
        page = DetailPage()
        try:
            base_invoice = {
                "id": 3,
                "status": "Pending_Review",
                "confidence_score": 0.9,
                "extracted_data": {
                    "invoice_number": "INV-1",
                    "date": "01-04-2026",
                    "vendor_name": "Vendor A",
                    "total_taxable_amount": 100.0,
                    "total_amount": 118.0,
                    "line_items": [],
                },
                "validation": {"is_valid": True, "errors": [], "warnings": [], "issues": []},
            }
            page.load_invoice(base_invoice)
            self.assertFalse(page.corrections_btn.isEnabled())

            page.metadata.fields["vendor_name"].setText("Vendor B")
            self.assertTrue(page.corrections_btn.isEnabled())

            saved_invoice = dict(base_invoice)
            saved_invoice["extracted_data"] = dict(base_invoice["extracted_data"])
            saved_invoice["extracted_data"]["vendor_name"] = "Vendor B"
            page.load_invoice(saved_invoice)
            self.assertEqual(page.metadata.fields["vendor_name"].text(), "Vendor B")
            self.assertFalse(page.corrections_btn.isEnabled())

            page.metadata.fields["vendor_name"].setText("Vendor C")
            self.assertTrue(page.corrections_btn.isEnabled())
            self.assertEqual(page.build_corrections()["vendor_name"], "Vendor C")
        finally:
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()
