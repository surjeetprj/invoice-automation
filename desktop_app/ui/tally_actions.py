from __future__ import annotations

"""TallyPrime action flows for the main desktop window."""

from typing import Any

from PySide6.QtWidgets import QMessageBox


class TallyActionsMixin:
    """Mixin containing direct TallyPrime confirmation and posting flows."""

    def tally_company_for_message(self) -> str:
        """Return the selected company name shown in direct TallyPrime dialogs."""
        selector = getattr(self, "company_selector", None)
        if selector is not None:
            current_text = getattr(selector, "currentText", None)
            if callable(current_text):
                company = str(current_text()).strip()
                if company:
                    return company
        settings = getattr(self, "current_settings", {}) or {}
        for key in ("tally_company", "selected_company"):
            company = str(settings.get(key) or "").strip()
            if company:
                return company
        return "the selected company"

    def tally_success_message(self, result: dict[str, Any], fallback: str, company: str) -> str:
        """Append selected-company context to a direct TallyPrime success message."""
        message = str(result.get("message") or fallback).strip()
        return f"{message}\n\nCompany: {company}"

    def post_invoice_to_tally(self, invoice_id: int) -> None:
        """Preflight and post an approved invoice to local TallyPrime."""
        company = self.tally_company_for_message()

        def preflight_done(result: dict[str, Any]) -> None:
            missing = result.get("missing_masters") or []
            create_missing = False
            if missing:
                message = f"TallyPrime is missing these masters for purchase voucher posting for company '{company}':\n\n"
                message += "\n".join(f"- {name}" for name in missing)
                message += "\n\nCreate these masters and post the purchase voucher?"
                if QMessageBox.question(self, "Post Purchase Voucher to TallyPrime", message) != QMessageBox.StandardButton.Yes:
                    return
                create_missing = True
            elif QMessageBox.question(
                self,
                "Post Purchase Voucher to TallyPrime",
                f"Post this approved invoice as a purchase voucher to TallyPrime company '{company}'?",
            ) != QMessageBox.StandardButton.Yes:
                return

            def posted(post_result: dict[str, Any]) -> None:
                QMessageBox.information(self, "TallyPrime", self.tally_success_message(post_result, "Invoice posted to TallyPrime.", company))
                self.open_invoice(invoice_id)

            self.run_task(
                lambda: self.workflow.post_invoice_to_tally(invoice_id, create_missing_masters=create_missing),
                posted,
            )

        self.run_task(lambda: self.workflow.tally_preflight(invoice_id), preflight_done)

    def post_invoice_items_to_tally(self, invoice_id: int) -> None:
        """Preflight and post reviewed line items to local TallyPrime."""
        company = self.tally_company_for_message()

        def preflight_done(result: dict[str, Any]) -> None:
            missing = result.get("missing_masters") or []
            create_missing = False
            if missing:
                message = f"TallyPrime is missing these masters for item-wise purchase voucher posting for company '{company}':\n\n"
                message += "\n".join(f"- {name}" for name in missing)
                message += "\n\nCreate these masters and post the item-wise purchase voucher?"
                if QMessageBox.question(self, "Post Item-wise Purchase Voucher to TallyPrime", message) != QMessageBox.StandardButton.Yes:
                    return
                create_missing = True
            elif QMessageBox.question(
                self,
                "Post Item-wise Purchase Voucher to TallyPrime",
                f"Post this approved invoice as an item-wise purchase voucher to TallyPrime company '{company}'?",
            ) != QMessageBox.StandardButton.Yes:
                return

            def posted(post_result: dict[str, Any]) -> None:
                QMessageBox.information(self, "TallyPrime", self.tally_success_message(post_result, "Invoice items posted to TallyPrime.", company))
                self.open_invoice(invoice_id)

            self.run_task(
                lambda: self.workflow.post_invoice_items_to_tally(invoice_id, create_missing_masters=create_missing),
                posted,
            )

        self.run_task(lambda: self.workflow.tally_inventory_preflight(invoice_id), preflight_done)

    def sync_vendor_master_to_tally(self, invoice_id: int) -> None:
        """Update only the vendor ledger master in TallyPrime."""
        company = self.tally_company_for_message()
        if QMessageBox.question(
            self,
            "Sync Vendor Ledger to TallyPrime",
            f"Update this vendor ledger in TallyPrime company '{company}' with extracted vendor details?",
        ) != QMessageBox.StandardButton.Yes:
            return

        def synced(result: dict[str, Any]) -> None:
            QMessageBox.information(self, "TallyPrime", self.tally_success_message(result, "Vendor master synced to TallyPrime.", company))

        self.run_task(lambda: self.workflow.sync_vendor_master_to_tally(invoice_id), synced)

    def sync_tally_system_ledgers(self, invoice_id: int) -> None:
        """Update purchase and GST ledger masters in TallyPrime."""
        company = self.tally_company_for_message()
        if QMessageBox.question(
            self,
            "Sync Purchase and GST Ledgers to TallyPrime",
            f"Update the purchase ledger and GST ledgers in TallyPrime company '{company}'?",
        ) != QMessageBox.StandardButton.Yes:
            return

        def synced(result: dict[str, Any]) -> None:
            QMessageBox.information(self, "TallyPrime", self.tally_success_message(result, "Purchase and GST ledgers synced to TallyPrime.", company))

        self.run_task(lambda: self.workflow.sync_tally_system_ledgers(invoice_id), synced)
