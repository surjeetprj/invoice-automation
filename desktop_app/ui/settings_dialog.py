from __future__ import annotations

"""Settings dialog for runtime-editable Tally defaults."""

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    """Dialog for editing TallyPrime runtime settings."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.tally_url = QLineEdit()
        self.company = QComboBox()
        self.company.setEditable(True)
        self.license_file = QLineEdit()
        self.timeout_seconds = QSpinBox()
        self.timeout_seconds.setRange(1, 300)
        self.vendor_parent = QLineEdit()
        self.default_stock_group = QLineEdit()
        self.purchase_ledger = QLineEdit()
        self.input_cgst = QLineEdit()
        self.input_sgst = QLineEdit()
        self.input_igst = QLineEdit()
        self.input_cess = QLineEdit()

        license_row = QHBoxLayout()
        license_row.setContentsMargins(0, 0, 0, 0)
        license_row.addWidget(self.license_file, stretch=1)
        self.browse_license_btn = QPushButton("Browse License")
        license_row.addWidget(self.browse_license_btn)

        form.addRow("Tally URL", self.tally_url)
        form.addRow("Company", self.company)
        form.addRow("License File", license_row)
        form.addRow("Timeout Seconds", self.timeout_seconds)
        form.addRow("Vendor Parent Ledger", self.vendor_parent)
        form.addRow("Default Stock Group", self.default_stock_group)
        form.addRow("Purchase Ledger", self.purchase_ledger)
        form.addRow("Input CGST Ledger", self.input_cgst)
        form.addRow("Input SGST Ledger", self.input_sgst)
        form.addRow("Input IGST Ledger", self.input_igst)
        form.addRow("Input CESS Ledger", self.input_cess)
        layout.addLayout(form)

        action_row = QHBoxLayout()
        self.refresh_companies_btn = QPushButton("Refresh Companies")
        self.test_connection_btn = QPushButton("Test Connection")
        action_row.addWidget(self.refresh_companies_btn)
        action_row.addWidget(self.test_connection_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.browse_license_btn.clicked.connect(self.browse_license)

    def load_settings(self, settings: dict[str, Any]) -> None:
        """Populate the dialog from a settings dictionary."""
        self.tally_url.setText(str(settings.get("tally_url") or ""))
        self.set_companies([str(settings.get("tally_company") or "")])
        self.company.setCurrentText(str(settings.get("tally_company") or ""))
        self.license_file.setText(str(settings.get("invoiceai_license_file") or ""))
        self.timeout_seconds.setValue(int(settings.get("tally_timeout_seconds") or 20))
        self.vendor_parent.setText(str(settings.get("tally_vendor_parent_ledger") or ""))
        self.default_stock_group.setText(str(settings.get("default_stock_group") or ""))
        self.purchase_ledger.setText(str(settings.get("purchase_ledger_name") or ""))
        self.input_cgst.setText(str(settings.get("input_cgst_ledger_name") or ""))
        self.input_sgst.setText(str(settings.get("input_sgst_ledger_name") or ""))
        self.input_igst.setText(str(settings.get("input_igst_ledger_name") or ""))
        self.input_cess.setText(str(settings.get("input_cess_ledger_name") or ""))

    def set_companies(self, companies: list[str]) -> None:
        """Replace company choices while preserving the current typed value."""
        current = self.company.currentText().strip()
        values = []
        for company in [current, *companies]:
            cleaned = company.strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)
        self.company.blockSignals(True)
        self.company.clear()
        self.company.addItems(values)
        self.company.setCurrentText(current)
        self.company.blockSignals(False)

    def settings_payload(self) -> dict[str, Any]:
        """Return edited settings for persistence or connection testing."""
        return {
            "tally_url": self.tally_url.text().strip(),
            "tally_company": self.company.currentText().strip(),
            "invoiceai_license_file": self.license_file.text().strip(),
            "tally_timeout_seconds": self.timeout_seconds.value(),
            "tally_vendor_parent_ledger": self.vendor_parent.text().strip(),
            "default_stock_group": self.default_stock_group.text().strip(),
            "purchase_ledger_name": self.purchase_ledger.text().strip(),
            "input_cgst_ledger_name": self.input_cgst.text().strip(),
            "input_sgst_ledger_name": self.input_sgst.text().strip(),
            "input_igst_ledger_name": self.input_igst.text().strip(),
            "input_cess_ledger_name": self.input_cess.text().strip(),
        }

    def browse_license(self) -> None:
        """Select a signed InvoiceAI license file."""
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select InvoiceAI License",
            self.license_file.text().strip(),
            "JSON Files (*.json);;All Files (*.*)",
        )
        if path:
            self.license_file.setText(path)
