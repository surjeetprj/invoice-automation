from __future__ import annotations

"""Settings-related actions for the main desktop window."""

from typing import Any

from PySide6.QtWidgets import QDialog, QMessageBox

from .settings_dialog import SettingsDialog


class SettingsActionsMixin:
    """Mixin containing top-bar and settings dialog actions."""

    def load_settings(self) -> None:
        """Load runtime settings into top-bar controls without contacting Tally."""
        def loaded(settings: dict[str, Any]) -> None:
            self.current_settings = settings.get("tally", {})
            self.set_company_selector(self.current_settings.get("tally_company", ""))

        self.run_task(self.workflow.get_settings, loaded)

    def set_company_selector(self, company: str) -> None:
        """Show the active Tally company in the top bar."""
        self.loading_settings = True
        try:
            current_values = [self.company_selector.itemText(index) for index in range(self.company_selector.count())]
            if company and company not in current_values:
                self.company_selector.addItem(company)
            self.company_selector.setCurrentText(company or "")
        finally:
            self.loading_settings = False

    def save_selected_company(self, *_args: Any) -> None:
        """Persist the company selected or typed in the top bar."""
        if self.loading_settings:
            return
        company = self.company_selector.currentText().strip()
        if company == str(self.current_settings.get("tally_company") or ""):
            return
        payload = {"selected_company": company, "tally_company": company}

        def saved(settings: dict[str, Any]) -> None:
            self.current_settings = settings.get("tally", {})
            self.set_company_selector(self.current_settings.get("tally_company", ""))

        self.run_task(self.workflow.save_settings, saved, {"tally": payload})

    def open_settings_dialog(self) -> None:
        """Open Tally settings dialog and persist accepted edits."""
        dialog = SettingsDialog(self)
        dialog.load_settings(self.current_settings)

        def refresh_companies() -> None:
            def loaded(companies: list[str]) -> None:
                dialog.set_companies(companies)
                QMessageBox.information(dialog, "TallyPrime", f"Loaded {len(companies)} compan{'y' if len(companies) == 1 else 'ies'}.")

            self.run_task(self.workflow.list_tally_companies, loaded, on_error=lambda err: QMessageBox.warning(dialog, "TallyPrime", err))

        def test_connection() -> None:
            def tested(result: dict[str, Any]) -> None:
                serial = result.get("serial_number") or "not available"
                companies = result.get("companies") or []
                if companies:
                    dialog.set_companies([str(company) for company in companies])
                dialog.set_serial_number(str(serial))
                QMessageBox.information(dialog, "TallyPrime", f"Connection verified. Serial: {serial}")

            self.run_task(
                self.workflow.test_tally_settings,
                tested,
                {"tally": dialog.settings_payload()},
                on_error=lambda err: QMessageBox.warning(dialog, "TallyPrime", err),
            )

        def refresh_masters() -> None:
            company = dialog.selected_company()

            def loaded(result: dict[str, Any]) -> None:
                dialog.set_ledgers([str(ledger) for ledger in result.get("ledgers", [])])
                dialog.set_stock_groups([str(group) for group in result.get("stock_groups", [])])
                QMessageBox.information(dialog, "TallyPrime", "Loaded ledger and stock group choices.")

            def load_options() -> dict[str, Any]:
                return {
                    "ledgers": self.workflow.list_tally_ledgers(company),
                    "stock_groups": self.workflow.list_tally_stock_groups(company),
                }

            self.run_task(load_options, loaded, on_error=lambda err: QMessageBox.warning(dialog, "TallyPrime", err))

        dialog.refresh_companies_btn.clicked.connect(refresh_companies)
        dialog.test_connection_btn.clicked.connect(test_connection)
        dialog.refresh_masters_btn.clicked.connect(refresh_masters)
        if dialog.exec() != QDialog.Accepted:
            return

        def saved(settings: dict[str, Any]) -> None:
            self.current_settings = settings.get("tally", {})
            self.set_company_selector(self.current_settings.get("tally_company", ""))

        self.run_task(self.workflow.save_settings, saved, {"tally": dialog.settings_payload()})

