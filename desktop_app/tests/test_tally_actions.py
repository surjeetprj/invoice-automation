from __future__ import annotations

"""Focused tests for direct TallyPrime UI action messages."""

import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from desktop_app.ui.tally_actions import TallyActionsMixin


class DummyCompanySelector:
    """Tiny stand-in for the top-bar company selector."""

    def __init__(self, company: str) -> None:
        self.company = company

    def currentText(self) -> str:
        return self.company


class DummyWorkflow:
    """Synchronous workflow stub for Tally action tests."""

    def __init__(self) -> None:
        self.purchase_missing: list[str] = []
        self.inventory_missing: list[str] = []
        self.create_missing_masters: bool | None = None
        self.create_missing_inventory_masters: bool | None = None

    def tally_preflight(self, invoice_id: int) -> dict[str, object]:
        return {"missing_masters": self.purchase_missing}

    def tally_inventory_preflight(self, invoice_id: int) -> dict[str, object]:
        return {"missing_masters": self.inventory_missing}

    def post_invoice_to_tally(self, invoice_id: int, *, create_missing_masters: bool = False) -> dict[str, object]:
        self.create_missing_masters = create_missing_masters
        return {"success": True, "message": "Invoice posted to TallyPrime."}

    def post_invoice_items_to_tally(self, invoice_id: int, *, create_missing_masters: bool = False) -> dict[str, object]:
        self.create_missing_inventory_masters = create_missing_masters
        return {"success": True, "message": "Invoice items posted to TallyPrime."}

    def sync_vendor_master_to_tally(self, invoice_id: int) -> dict[str, object]:
        return {"success": True, "message": "Vendor master synced to TallyPrime."}

    def sync_tally_system_ledgers(self, invoice_id: int) -> dict[str, object]:
        return {"success": True, "message": "Purchase and GST ledgers synced to TallyPrime."}


class DummyWindow(TallyActionsMixin):
    """Minimal host for TallyActionsMixin methods."""

    def __init__(self, company: str = "Demo Company") -> None:
        self.company_selector = DummyCompanySelector(company)
        self.current_settings = {"tally_company": company}
        self.workflow = DummyWorkflow()
        self.opened_invoice_ids: list[int] = []

    def run_task(self, task, callback, *args, **_kwargs):
        callback(task(*args))

    def open_invoice(self, invoice_id: int) -> None:
        self.opened_invoice_ids.append(invoice_id)


class TallyActionsMessageTests(unittest.TestCase):
    """Confirm direct Tally UI messages always name the selected company."""

    def assert_dialogs_include_company(self, question, information, company: str) -> None:
        question_message = question.call_args.args[2]
        success_message = information.call_args.args[2]
        self.assertIn(f"'{company}'", question_message)
        self.assertIn(f"Company: {company}", success_message)

    def test_ledger_only_posting_messages_include_selected_company(self) -> None:
        window = DummyWindow("Demo Company")
        with (
            patch("desktop_app.ui.tally_actions.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question,
            patch("desktop_app.ui.tally_actions.QMessageBox.information") as information,
        ):
            window.post_invoice_to_tally(42)

        self.assertFalse(window.workflow.create_missing_masters)
        self.assertEqual(window.opened_invoice_ids, [42])
        self.assert_dialogs_include_company(question, information, "Demo Company")

    def test_ledger_only_missing_master_confirmation_includes_selected_company(self) -> None:
        window = DummyWindow("Demo Company")
        window.workflow.purchase_missing = ["Vendor Ledger: Vendor A under Sundry Creditors"]
        with (
            patch("desktop_app.ui.tally_actions.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question,
            patch("desktop_app.ui.tally_actions.QMessageBox.information"),
        ):
            window.post_invoice_to_tally(42)

        self.assertTrue(window.workflow.create_missing_masters)
        self.assertIn("for company 'Demo Company'", question.call_args.args[2])
        self.assertIn("Vendor Ledger: Vendor A", question.call_args.args[2])

    def test_item_wise_posting_messages_include_selected_company(self) -> None:
        window = DummyWindow("Demo Company")
        with (
            patch("desktop_app.ui.tally_actions.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question,
            patch("desktop_app.ui.tally_actions.QMessageBox.information") as information,
        ):
            window.post_invoice_items_to_tally(42)

        self.assertFalse(window.workflow.create_missing_inventory_masters)
        self.assertEqual(window.opened_invoice_ids, [42])
        self.assert_dialogs_include_company(question, information, "Demo Company")

    def test_item_wise_missing_master_confirmation_includes_selected_company(self) -> None:
        window = DummyWindow("Demo Company")
        window.workflow.inventory_missing = ["Stock Item Master: Consulting Service under Primary"]
        with (
            patch("desktop_app.ui.tally_actions.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question,
            patch("desktop_app.ui.tally_actions.QMessageBox.information"),
        ):
            window.post_invoice_items_to_tally(42)

        self.assertTrue(window.workflow.create_missing_inventory_masters)
        self.assertIn("for company 'Demo Company'", question.call_args.args[2])
        self.assertIn("Stock Item Master: Consulting Service", question.call_args.args[2])

    def test_vendor_sync_messages_include_selected_company(self) -> None:
        window = DummyWindow("Demo Company")
        with (
            patch("desktop_app.ui.tally_actions.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question,
            patch("desktop_app.ui.tally_actions.QMessageBox.information") as information,
        ):
            window.sync_vendor_master_to_tally(42)

        self.assert_dialogs_include_company(question, information, "Demo Company")

    def test_gst_ledger_sync_messages_include_selected_company(self) -> None:
        window = DummyWindow("Demo Company")
        with (
            patch("desktop_app.ui.tally_actions.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as question,
            patch("desktop_app.ui.tally_actions.QMessageBox.information") as information,
        ):
            window.sync_tally_system_ledgers(42)

        self.assert_dialogs_include_company(question, information, "Demo Company")


if __name__ == "__main__":
    unittest.main()
