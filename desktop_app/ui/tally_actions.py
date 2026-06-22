from __future__ import annotations

"""TallyPrime action flows for the main desktop window."""

from typing import Any

from PySide6.QtWidgets import QMessageBox


class TallyActionsMixin:
    """Mixin containing direct TallyPrime confirmation and posting flows."""

    def post_invoice_to_tally(self, invoice_id: int) -> None:
        """Preflight and post an approved invoice to local TallyPrime."""
        def preflight_done(result: dict[str, Any]) -> None:
            missing = result.get("missing_masters") or []
            create_missing = False
            if missing:
                message = "TallyPrime is missing these masters for purchase voucher posting:\n\n"
                message += "\n".join(f"- {name}" for name in missing)
                message += "\n\nCreate these masters and post the purchase voucher?"
                if QMessageBox.question(self, "Post Purchase Voucher to TallyPrime", message) != QMessageBox.StandardButton.Yes:
                    return
                create_missing = True
            elif QMessageBox.question(
                self,
                "Post Purchase Voucher to TallyPrime",
                "Post this approved invoice as a purchase voucher to TallyPrime?",
            ) != QMessageBox.StandardButton.Yes:
                return

            def posted(post_result: dict[str, Any]) -> None:
                QMessageBox.information(self, "TallyPrime", post_result.get("message", "Invoice posted to TallyPrime."))
                self.open_invoice(invoice_id)

            self.run_task(
                lambda: self.workflow.post_invoice_to_tally(invoice_id, create_missing_masters=create_missing),
                posted,
            )

        self.run_task(lambda: self.workflow.tally_preflight(invoice_id), preflight_done)

    def post_invoice_items_to_tally(self, invoice_id: int) -> None:
        """Preflight and post reviewed line items to local TallyPrime."""
        def preflight_done(result: dict[str, Any]) -> None:
            missing = result.get("missing_masters") or []
            create_missing = False
            if missing:
                message = "TallyPrime is missing these masters for item-wise purchase voucher posting:\n\n"
                message += "\n".join(f"- {name}" for name in missing)
                message += "\n\nCreate these masters and post the item-wise purchase voucher?"
                if QMessageBox.question(self, "Post Item-wise Purchase Voucher to TallyPrime", message) != QMessageBox.StandardButton.Yes:
                    return
                create_missing = True
            elif QMessageBox.question(
                self,
                "Post Item-wise Purchase Voucher to TallyPrime",
                "Post this approved invoice as an item-wise purchase voucher to TallyPrime?",
            ) != QMessageBox.StandardButton.Yes:
                return

            def posted(post_result: dict[str, Any]) -> None:
                QMessageBox.information(self, "TallyPrime", post_result.get("message", "Invoice items posted to TallyPrime."))
                self.open_invoice(invoice_id)

            self.run_task(
                lambda: self.workflow.post_invoice_items_to_tally(invoice_id, create_missing_masters=create_missing),
                posted,
            )

        self.run_task(lambda: self.workflow.tally_inventory_preflight(invoice_id), preflight_done)

    def sync_vendor_master_to_tally(self, invoice_id: int) -> None:
        """Update only the vendor ledger master in TallyPrime."""
        if QMessageBox.question(
            self,
            "Sync Vendor Ledger to TallyPrime",
            "Update this vendor ledger in TallyPrime with extracted vendor details?",
        ) != QMessageBox.StandardButton.Yes:
            return

        def synced(result: dict[str, Any]) -> None:
            QMessageBox.information(self, "TallyPrime", result.get("message", "Vendor master synced to TallyPrime."))

        self.run_task(lambda: self.workflow.sync_vendor_master_to_tally(invoice_id), synced)

    def sync_tally_system_ledgers(self, invoice_id: int) -> None:
        """Update purchase and GST ledger masters in TallyPrime."""
        if QMessageBox.question(
            self,
            "Sync Purchase and GST Ledgers to TallyPrime",
            "Update the purchase ledger and GST ledgers in TallyPrime?",
        ) != QMessageBox.StandardButton.Yes:
            return

        def synced(result: dict[str, Any]) -> None:
            QMessageBox.information(self, "TallyPrime", result.get("message", "Purchase and GST ledgers synced to TallyPrime."))

        self.run_task(lambda: self.workflow.sync_tally_system_ledgers(invoice_id), synced)

