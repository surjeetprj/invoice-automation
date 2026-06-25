from __future__ import annotations

"""Settings dialog for runtime-editable Tally defaults."""

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
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
        self.setMinimumWidth(560)
        self.company_mappings: dict[str, dict[str, Any]] = {}
        self.default_mapping: dict[str, Any] = {}
        self.loading_settings = False

        layout = QVBoxLayout(self)

        tally_group = QGroupBox("Tally Settings")
        tally_form = QFormLayout(tally_group)
        self.tally_url = QLineEdit()
        self.serial_number = QLineEdit()
        self.serial_number.setReadOnly(True)
        self.serial_number.setPlaceholderText("Use Test Connection to detect")
        self.timeout_seconds = QSpinBox()
        self.timeout_seconds.setRange(1, 300)

        tally_form.addRow("Tally URL", self.tally_url)
        tally_form.addRow("Tally Serial Number", self.serial_number)
        tally_form.addRow("Timeout Seconds", self.timeout_seconds)
        layout.addWidget(tally_group)

        company_group = QGroupBox("Company Settings")
        company_form = QFormLayout(company_group)
        self.company = self.editable_combo()
        company_form.addRow("Select Company", self.company)
        layout.addWidget(company_group)

        mapping_group = QGroupBox("Ledger Mapping")
        mapping_form = QFormLayout(mapping_group)
        self.vendor_parent = self.editable_combo()
        self.default_stock_group = self.editable_combo()
        self.purchase_ledger = self.editable_combo()
        self.input_cgst = self.editable_combo()
        self.input_sgst = self.editable_combo()
        self.input_igst = self.editable_combo()
        self.input_cess = self.editable_combo()
        mapping_form.addRow("Vender A/C Group", self.vendor_parent)
        mapping_form.addRow("Stock Group", self.default_stock_group)
        mapping_form.addRow("Purchase Ledger", self.purchase_ledger)
        mapping_form.addRow("Input CGST Ledger", self.input_cgst)
        mapping_form.addRow("Input SGST Ledger", self.input_sgst)
        mapping_form.addRow("Input IGST Ledger", self.input_igst)
        mapping_form.addRow("Input CESS Ledger", self.input_cess)
        layout.addWidget(mapping_group)

        action_row = QHBoxLayout()
        self.refresh_companies_btn = QPushButton("Refresh Companies")
        self.refresh_masters_btn = QPushButton("Refresh Ledgers / Groups")
        self.test_connection_btn = QPushButton("Test Connection")
        action_row.addWidget(self.refresh_companies_btn)
        action_row.addWidget(self.refresh_masters_btn)
        action_row.addWidget(self.test_connection_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.company.activated.connect(lambda _index: self.load_company_mapping())
        if self.company.lineEdit():
            self.company.lineEdit().editingFinished.connect(self.load_company_mapping)

    def editable_combo(self) -> QComboBox:
        """Create an editable combo box used for Tally master selections."""
        combo = QComboBox()
        combo.setEditable(True)
        return combo

    def load_settings(self, settings: dict[str, Any]) -> None:
        """Populate the dialog from a settings dictionary."""
        self.loading_settings = True
        try:
            self.company_mappings = {
                str(name): dict(mapping)
                for name, mapping in (settings.get("company_mappings") or {}).items()
                if isinstance(mapping, dict)
            }
            self.default_mapping = self.mapping_from_settings(settings)
            self.tally_url.setText(str(settings.get("tally_url") or ""))
            selected_company = str(settings.get("selected_company") or settings.get("tally_company") or "")
            self.set_companies([selected_company, *self.company_mappings.keys()])
            self.company.setCurrentText(selected_company)
            self.serial_number.setText(str(settings.get("tally_serial_number_display") or ""))
            self.timeout_seconds.setValue(int(settings.get("tally_timeout_seconds") or 20))
            self.load_company_mapping(settings)
        finally:
            self.loading_settings = False

    def load_company_mapping(self, fallback: dict[str, Any] | None = None) -> None:
        """Load ledger mapping fields for the currently selected company."""
        if self.loading_settings and fallback is None:
            return
        company = self.selected_company()
        mapping = dict(self.company_mappings.get(company, {}))
        if not mapping and fallback:
            mapping = self.mapping_from_settings(fallback)
        if not mapping:
            mapping = self.default_mapping
        self.set_combo_text(self.vendor_parent, str(mapping.get("tally_vendor_parent_ledger") or ""))
        self.set_combo_text(self.default_stock_group, str(mapping.get("default_stock_group") or ""))
        self.set_combo_text(self.purchase_ledger, str(mapping.get("purchase_ledger_name") or ""))
        self.set_combo_text(self.input_cgst, str(mapping.get("input_cgst_ledger_name") or ""))
        self.set_combo_text(self.input_sgst, str(mapping.get("input_sgst_ledger_name") or ""))
        self.set_combo_text(self.input_igst, str(mapping.get("input_igst_ledger_name") or ""))
        self.set_combo_text(self.input_cess, str(mapping.get("input_cess_ledger_name") or ""))

    def mapping_from_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Return default ledger mapping values from environment/config-backed settings."""
        default_mapping = settings.get("default_company_mapping")
        if isinstance(default_mapping, dict):
            return dict(default_mapping)
        return {
            "tally_vendor_parent_ledger": settings.get("tally_vendor_parent_ledger"),
            "default_stock_group": settings.get("default_stock_group"),
            "purchase_ledger_name": settings.get("purchase_ledger_name"),
            "input_cgst_ledger_name": settings.get("input_cgst_ledger_name"),
            "input_sgst_ledger_name": settings.get("input_sgst_ledger_name"),
            "input_igst_ledger_name": settings.get("input_igst_ledger_name"),
            "input_cess_ledger_name": settings.get("input_cess_ledger_name"),
        }

    def set_companies(self, companies: list[str]) -> None:
        """Replace company choices while preserving the current typed value."""
        current = self.company.currentText().strip()
        values = []
        for company in [current, *companies]:
            cleaned = str(company or "").strip()
            if cleaned and cleaned not in values:
                values.append(cleaned)
        self.company.blockSignals(True)
        self.company.clear()
        self.company.addItems(values)
        self.company.setCurrentText(current)
        self.company.blockSignals(False)

    def set_ledgers(self, ledgers: list[str]) -> None:
        """Populate all ledger-related combo boxes from TallyPrime names."""
        for combo in (self.vendor_parent, self.purchase_ledger, self.input_cgst, self.input_sgst, self.input_igst, self.input_cess):
            self.set_combo_values(combo, ledgers)

    def set_stock_groups(self, stock_groups: list[str]) -> None:
        """Populate the default stock group combo box."""
        self.set_combo_values(self.default_stock_group, stock_groups)

    def set_options(self, options: dict[str, Any]) -> None:
        """Populate mapping choices filtered by category from TallyPrime."""
        groups = options.get("groups") or []
        purchase_ledgers = options.get("purchase_ledgers") or []
        duty_ledgers = options.get("duty_ledgers") or []
        stock_groups = options.get("stock_groups") or []
        
        self.set_combo_values(self.vendor_parent, groups)
        self.set_combo_values(self.default_stock_group, stock_groups)
        self.set_combo_values(self.purchase_ledger, purchase_ledgers)
        
        for combo in (self.input_cgst, self.input_sgst, self.input_igst, self.input_cess):
            self.set_combo_values(combo, duty_ledgers)

    def set_serial_number(self, serial_number: str) -> None:
        """Display the detected Tally serial number without persisting it."""
        self.serial_number.setText(serial_number)

    def settings_payload(self) -> dict[str, Any]:
        """Return edited settings for persistence or connection testing."""
        company = self.selected_company()
        return {
            "tally_url": self.tally_url.text().strip(),
            "selected_company": company,
            "tally_company": company,
            "tally_timeout_seconds": self.timeout_seconds.value(),
            "tally_vendor_parent_ledger": self.combo_text(self.vendor_parent),
            "default_stock_group": self.combo_text(self.default_stock_group),
            "purchase_ledger_name": self.combo_text(self.purchase_ledger),
            "input_cgst_ledger_name": self.combo_text(self.input_cgst),
            "input_sgst_ledger_name": self.combo_text(self.input_sgst),
            "input_igst_ledger_name": self.combo_text(self.input_igst),
            "input_cess_ledger_name": self.combo_text(self.input_cess),
        }

    def selected_company(self) -> str:
        """Return the current company selector text."""
        return self.company.currentText().strip()

    def combo_text(self, combo: QComboBox) -> str:
        """Return trimmed text from an editable combo box."""
        return combo.currentText().strip()

    def set_combo_text(self, combo: QComboBox, value: str) -> None:
        """Set combo text while keeping the value selectable."""
        cleaned = str(value or "").strip()
        if cleaned and combo.findText(cleaned) < 0:
            combo.addItem(cleaned)
        combo.setCurrentText(cleaned)

    def set_combo_values(self, combo: QComboBox, values: list[str]) -> None:
        """Replace combo choices while preserving current text."""
        current = combo.currentText().strip()
        unique = []
        for value in [current, *values]:
            cleaned = str(value or "").strip()
            if cleaned and cleaned not in unique:
                unique.append(cleaned)
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(unique)
        combo.setCurrentText(current)
        combo.blockSignals(False)
