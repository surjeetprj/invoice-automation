from __future__ import annotations

"""Regression tests for the invoice detail review UI."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from desktop_app.ui.constants import FIELD_GROUPS, LINE_COLUMNS, REQUIRED_METADATA_FIELDS
from desktop_app.ui.dashboard_page import DashboardPage
from desktop_app.ui.detail_page import DetailPage
from desktop_app.ui.main_window import MainWindow
from desktop_app.ui.settings_dialog import SettingsDialog
from desktop_app.ui.upload_page import UploadPage
from desktop_app.ui.widgets.tally_mappings_table import TallyMappingsTable
from desktop_app.ui.widgets.worker import Worker


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
            self.assertEqual([page.tabs.tabText(index) for index in range(page.tabs.count())], ["Metadata", "Validation", "Audit Logs", "Raw Markdown"])
            self.assertEqual(page.splitter.widget(1).minimumWidth(), 360)
            labels = [action.text() for action in page.export_btn.menu().actions()]
            self.assertIn("\u2715 JSON", labels)
            self.assertIn("\u2715 Tally XML", labels)
            self.assertNotIn("\u2715 CSV", labels)
            self.assertNotIn("\u2715 ERPNext", labels)
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
                    "ai_call_count": 3,
                    "reprocess_count": 2,
                }
            )
            self.assertEqual(page.summary.text(), "Confidence: 50% | AI calls: 3 | Reprocesses: 2")
            for field in REQUIRED_METADATA_FIELDS:
                self.assertEqual(page.metadata.fields[field].objectName(), "requiredMissing")
            self.assertTrue(page.approve_btn.isEnabled())
            self.assertIn("At least one line item", page.metadata.line_items_section.hint.text())
        finally:
            page.deleteLater()

    def test_error_banner_visibility_on_ai_errors(self) -> None:
        """The detail page error banner should show only when AI/parsing errors exist."""
        page = DetailPage()
        try:
            # 1. No AI errors
            page.load_invoice({
                "id": 1,
                "status": "Extracted",
                "extracted_data": {},
                "validation": {"issues": []},
            })
            self.assertTrue(page.error_banner.isHidden())

            # 2. AI Quota error
            page.load_invoice({
                "id": 2,
                "status": "Pending_Review",
                "extracted_data": {},
                "validation": {
                    "issues": [{"severity": "error", "message": "Rate limit exceeded", "field": "AI Quota"}]
                },
            })
            self.assertFalse(page.error_banner.isHidden())
            self.assertIn("Processing Error: Rate limit exceeded", page.error_banner.text())

            # 3. AI Parser error
            page.load_invoice({
                "id": 3,
                "status": "Pending_Review",
                "extracted_data": {},
                "validation": {
                    "issues": [{"severity": "error", "message": "Failed to parse JSON response", "field": "AI Parser"}]
                },
            })
            self.assertFalse(page.error_banner.isHidden())
            self.assertIn("Processing Error: Failed to parse JSON response", page.error_banner.text())

            # 4. Cleared/no error again
            page.load_invoice({
                "id": 4,
                "status": "Extracted",
                "extracted_data": {},
                "validation": {"issues": []},
            })
            self.assertTrue(page.error_banner.isHidden())
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

    def test_loading_many_line_items_does_not_recurse_during_validation(self) -> None:
        """Line-item validation should not emit recursive dirty-state updates while loading."""
        page = DetailPage()
        try:
            line_items = [
                {
                    "sr_no": index,
                    "item_name": f"Item {index}",
                    "description": f"Item {index}",
                    "quantity": 10.0,
                    "unit": "PCS",
                    "rate": 5.0,
                    "discount": 0.0,
                    "taxable_value": 50.0,
                    "taxes": [{"tax_type": "IGST", "tax_rate": 12.0, "taxable_amount": 50.0, "tax_amount": 6.0}],
                    "cess_amount": 0.0,
                    "total": 56.0,
                }
                for index in range(1, 11)
            ]
            page.load_invoice(
                {
                    "id": 4,
                    "status": "Pending_Review",
                    "confidence_score": 1.0,
                    "extracted_data": {
                        "invoice_number": "INV-4",
                        "date": "01-04-2026",
                        "vendor_name": "Vendor A",
                        "total_taxable_amount": 500.0,
                        "total_amount": 560.0,
                        "line_items": line_items,
                    },
                    "validation": {"is_valid": True, "errors": [], "warnings": [], "issues": []},
                }
            )

            self.assertEqual(page.line_items.table.rowCount(), 10)
            self.assertFalse(page.dirty)
            self.assertFalse(page.corrections_btn.isEnabled())
            self.assertTrue(page.approve_btn.isEnabled())
        finally:
            page.deleteLater()
    def test_approved_invoice_allows_corrections_and_export_only(self) -> None:
        """Approved invoices can be corrected again, while export stays available."""
        page = DetailPage()
        try:
            page.load_invoice(
                {
                    "id": 5,
                    "status": "Approved",
                    "confidence_score": 1.0,
                    "extracted_data": {
                        "invoice_number": "INV-5",
                        "date": "01-04-2026",
                        "vendor_name": "Vendor A",
                        "total_taxable_amount": 100.0,
                        "total_amount": 118.0,
                        "line_items": [],
                    },
                    "validation": {"is_valid": True, "errors": [], "warnings": [], "issues": []},
                }
            )
            self.assertFalse(page.approve_btn.isEnabled())
            self.assertFalse(page.reject_btn.isEnabled())
            self.assertTrue(page.export_btn.isEnabled())
            self.assertFalse(page.corrections_btn.isEnabled())

            page.metadata.fields["vendor_name"].setText("Corrected Vendor")
            self.assertTrue(page.corrections_btn.isEnabled())
            self.assertTrue(page.export_btn.isEnabled())
        finally:
            page.deleteLater()


    def test_dashboard_usage_date_and_cards_render_stats(self) -> None:
        """Dashboard should expose a default date picker and render usage KPI cards."""
        page = DashboardPage()
        try:
            self.assertEqual(page.usage_from_date(), QDate.currentDate().toString("yyyy-MM-dd"))
            for label in ("Total Invoices", "Usage Since Date", "AI Calls", "Reprocesses", "Pending Review"):
                self.assertNotIn(label, page.cards)
            self.assertGreaterEqual(page.usage_chart.canvas.minimumHeight(), 280)
            self.assertGreaterEqual(page.usage_chart.canvas.sizeHint().height(), 300)
            self.assertEqual(page.status_chart.title_label.text(), "Invoice Status Distribution")
            self.assertIs(page.usage_chart.controls.itemAt(1).widget(), page.usage_from)

            page.set_stats(
                {
                    "total_invoices": 4,
                    "avg_processing_time_ms": 1500,
                    "total_approved": 2,
                    "total_pending_review": 1,
                    "total_usage_count": 7,
                    "ai_calls_since_date": 5,
                    "reprocesses_since_date": 2,
                    "status_distribution": {"Approved": 2, "Pending_Review": 1},
                }
            )

            self.assertEqual(
                [(label, count) for label, count, _color in page.usage_chart.segments],
                [("AI Calls", 5), ("Reprocesses", 2)],
            )
            self.assertEqual(
                [(label, count) for label, count, _color in page.status_chart.segments],
                [("Approved", 2), ("Pending Review", 1)],
            )
        finally:
            page.deleteLater()

    def test_dashboard_date_change_requests_refresh(self) -> None:
        """Changing the usage date should refresh dashboard stats."""
        page = DashboardPage()
        refreshes = []
        try:
            page.refresh_requested.connect(lambda: refreshes.append(page.usage_from_date()))
            page.usage_from.setDate(QDate(2026, 6, 15))
            self.assertEqual(refreshes[-1], "2026-06-15")
        finally:
            page.deleteLater()


    def test_dashboard_charts_show_empty_states(self) -> None:
        """Dashboard donuts should keep empty-state labels when no chart data exists."""
        page = DashboardPage()
        try:
            page.set_stats(
                {
                    "total_invoices": 0,
                    "total_usage_count": 0,
                    "ai_calls_since_date": 0,
                    "reprocesses_since_date": 0,
                    "status_distribution": {},
                }
            )

            self.assertEqual(page.usage_chart.segments, [])
            self.assertEqual(page.status_chart.segments, [])
            self.assertEqual(page.usage_chart.canvas.toolTip(), "No usage")
            self.assertEqual(page.status_chart.canvas.toolTip(), "No invoices")
            self.assertEqual(page.usage_chart.legend_labels[0].text(), "No usage")
            self.assertEqual(page.status_chart.legend_labels[0].text(), "No invoices")
        finally:
            page.deleteLater()


    def test_main_window_uses_top_bar_navigation(self) -> None:
        """The app shell should use a top bar instead of the old sidebar splitter."""

        def run_immediately(self, task, callback, *args, on_error=None):
            try:
                callback(task(*args))
            except Exception as exc:
                if on_error:
                    on_error(str(exc))
                else:
                    raise

        class FakeWorkflow:
            def initialize(self):
                return None

            def health(self):
                return {"status": "ok"}

            def stats(self, usage_from_date=None):
                return {"usage_from_date": usage_from_date}

            def list_invoices(self):
                return {"invoices": []}


            def get_settings(self):
                return {"tally": {"tally_company": "Demo Company"}}
        with (
            patch("desktop_app.ui.main_window.DesktopWorkflow", return_value=FakeWorkflow()),
            patch.object(MainWindow, "run_task", new=run_immediately),
        ):
            window = MainWindow()
            self.app.processEvents()
            try:
                central = window.centralWidget()
                self.assertEqual(central.layout().count(), 2)
                top_bar = central.layout().itemAt(0).widget()
                self.assertEqual(top_bar.objectName(), "topBar")
                self.assertFalse(hasattr(window, "root_splitter"))
                self.assertEqual(set(window.nav_buttons), {"dashboard", "invoices", "upload"})
                self.assertEqual(window.company_selector.currentText(), "Demo Company")
                self.assertEqual(window.settings_btn.text(), "Settings")

                window.show_invoices()
                self.assertTrue(window.nav_buttons["invoices"].property("active"))
                self.assertFalse(window.nav_buttons["dashboard"].property("active"))

                window.reviewer.setText("approver")
                self.assertEqual(window.reviewer_name(), "approver")
            finally:
                window.close()
                window.deleteLater()

    def test_settings_dialog_round_trips_default_stock_group(self) -> None:
        """Settings dialog should include editable dropdowns and read-only serial display."""
        dialog = SettingsDialog()
        try:
            dialog.load_settings(
                {
                    "tally_company": "Demo Company",
                    "company_mappings": {
                        "Demo Company": {
                            "default_stock_group": "Software Services",
                            "purchase_ledger_name": "Purchase A",
                        }
                    },
                }
            )
            self.assertEqual(dialog.default_stock_group.currentText(), "Software Services")
            self.assertTrue(dialog.serial_number.isReadOnly())

            dialog.set_ledgers(["Purchase A", "Input CGST"])
            dialog.set_stock_groups(["Software Services", "Licenses"])
            dialog.default_stock_group.setCurrentText("Licenses")
            self.assertEqual(dialog.settings_payload()["default_stock_group"], "Licenses")
            self.assertEqual(dialog.settings_payload()["selected_company"], "Demo Company")
            self.assertNotIn("tally_serial_number", dialog.settings_payload())
        finally:
            dialog.deleteLater()

    def test_settings_dialog_uses_default_mapping_after_master_refresh(self) -> None:
        """Unsaved company mappings should keep env defaults when Tally masters refresh."""
        dialog = SettingsDialog()
        try:
            dialog.load_settings(
                {
                    "selected_company": "New Company",
                    "default_company_mapping": {
                        "tally_vendor_parent_ledger": "Sundry Creditors",
                        "default_stock_group": "Primary",
                        "purchase_ledger_name": "Purchase Account",
                        "input_cgst_ledger_name": "Input CGST",
                        "input_sgst_ledger_name": "Input SGST",
                        "input_igst_ledger_name": "Input IGST",
                        "input_cess_ledger_name": "Input CESS",
                    },
                    "company_mappings": {},
                }
            )

            self.assertEqual(dialog.vendor_parent.currentText(), "Sundry Creditors")
            self.assertEqual(dialog.default_stock_group.currentText(), "Primary")
            self.assertEqual(dialog.purchase_ledger.currentText(), "Purchase Account")

            dialog.set_ledgers(["Custom Purchase", "Input CGST"])
            dialog.set_stock_groups(["Software Services"])
            self.assertEqual(dialog.vendor_parent.currentText(), "Sundry Creditors")
            self.assertEqual(dialog.default_stock_group.currentText(), "Primary")
            self.assertEqual(dialog.purchase_ledger.currentText(), "Purchase Account")

            dialog.purchase_ledger.setCurrentText("Custom Purchase")
            self.assertEqual(dialog.settings_payload()["purchase_ledger_name"], "Custom Purchase")
        finally:
            dialog.deleteLater()

    def test_tally_mappings_table_preserves_company_context_in_changed_rows(self) -> None:
        """Mapping edits should keep the company used when the row was generated."""
        table = TallyMappingsTable()
        try:
            table.load_mappings(
                [
                    {
                        "mapping_type": "VENDOR_LEDGER",
                        "source_value": "Shree Medical",
                        "company_name": "Company A",
                        "tally_value": "Shree Medical",
                        "is_active": "Y",
                        "candidates": ["Shree Medical Agencies"],
                    }
                ]
            )
            combo = table.table.cellWidget(0, 2)
            combo.setCurrentText("Shree Medical Agencies")

            changed = table.changed_values()
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]["company_name"], "Company A")
            self.assertEqual(changed[0]["tally_value"], "Shree Medical Agencies")
        finally:
            table.deleteLater()

    def test_upload_page_status_replaces_processing_steps(self) -> None:
        """Upload status should show only one latest processing message."""
        page = UploadPage()
        try:
            page.set_busy(True, "Starting invoice processing...")
            page.set_activity({"message": "Checking file type and size...", "level": "info"})
            page.set_activity({"message": "Sending invoice content to Gemini...", "level": "info"})

            self.assertFalse(page.drop_zone.isEnabled())
            self.assertFalse(page.progress.isHidden())
            self.assertEqual(page.status.text(), "Sending invoice content to Gemini...")
            self.assertEqual(page.status.objectName(), "muted")

            page.set_busy(True, "Starting another invoice...")
            self.assertEqual(page.status.text(), "Starting another invoice...")

            page.set_busy(False, "Upload complete.")
            self.assertTrue(page.drop_zone.isEnabled())
            self.assertTrue(page.progress.isHidden())
            self.assertEqual(page.status.text(), "Upload complete.")
        finally:
            page.deleteLater()

    def test_main_window_upload_progress_reaches_upload_page(self) -> None:
        """Worker progress events should update the upload status message."""

        class FakeUpload:
            def __init__(self) -> None:
                self.busy_states = []
                self.status_messages = []

            def set_busy(self, busy, message=""):
                self.busy_states.append((busy, message))

            def set_activity(self, payload):
                self.status_messages.append(payload)

        class DummyWindow:
            def __init__(self) -> None:
                self.upload = FakeUpload()
                self.workflow = self
                self.opened_invoice_id = None

            def upload_invoice(self, path, progress_callback=None):
                progress_callback({"message": "Checking file type and size...", "level": "info"})
                progress_callback({"message": "Invoice is ready for review.", "level": "info"})
                return {"id": 42}

            def run_task(self, task, callback, *args, on_error=None, on_progress=None):
                result = task(*args, progress_callback=on_progress)
                callback(result)

            def open_invoice(self, invoice_id):
                self.opened_invoice_id = invoice_id

        window = DummyWindow()
        MainWindow.upload_invoice(window, "invoice.pdf")

        self.assertEqual(window.upload.busy_states[0], (True, "Starting invoice processing..."))
        self.assertEqual(window.upload.busy_states[-1], (False, "Upload complete."))
        self.assertEqual([event["message"] for event in window.upload.status_messages], [
            "Checking file type and size...",
            "Invoice is ready for review.",
        ])
        self.assertEqual(window.opened_invoice_id, 42)

    def test_worker_emits_progress_events_from_callback(self) -> None:
        """A callback-aware worker task should emit progress events before completion."""
        progress_events = []
        completed = []

        def task(progress_callback=None):
            progress_callback({"message": "Working...", "level": "info"})
            return "done"

        worker = Worker(task, progress_callback_name="progress_callback")
        worker.signals.progress.connect(progress_events.append)
        worker.signals.completed.connect(completed.append)
        worker.run()

        self.assertEqual(progress_events, [{"message": "Working...", "level": "info"}])
        self.assertTrue(completed[0].success)
        self.assertEqual(completed[0].result, "done")


if __name__ == "__main__":
    unittest.main()
