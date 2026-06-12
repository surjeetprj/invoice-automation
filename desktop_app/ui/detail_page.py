from __future__ import annotations

"""Invoice detail and human review page."""

import copy
from typing import Any

from PySide6.QtCore import QModelIndex, Signal
from PySide6.QtGui import QAction, QColor, QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyledItemDelegate,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QMenu,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from .constants import FIELD_GROUPS, LINE_COLUMNS, LINE_FLOAT_FIELDS, NUMERIC_FIELDS, TAX_AMOUNT_FIELDS, TAX_COMPONENTS, TAX_RATE_FIELDS
from .widgets.audit_pane import AuditPane
from .widgets.pdf_preview import PdfPreview
from .widgets.validation_pane import ValidationPane

LINE_INT_FIELDS = {"sr_no"}
INVALID_CELL_COLOR = QColor("#fee2e2")
VALID_CELL_COLOR = QColor("#ffffff")


class MetadataForm(QWidget):
    """Editable invoice metadata form grouped by business section."""

    changed = Signal()

    def __init__(self) -> None:
        """Build all metadata fields and group containers."""
        super().__init__()
        self.fields: dict[str, QLineEdit] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        for group, fields in FIELD_GROUPS.items():
            section = QFrame()
            section.setObjectName("subsection")
            section_layout = QVBoxLayout(section)
            label = QLabel(group)
            label.setObjectName("sectionTitle")
            form = QFormLayout()
            for field in fields:
                edit = QLineEdit()
                if field in NUMERIC_FIELDS:
                    edit.setValidator(QDoubleValidator(bottom=-999999999.0, top=999999999.0, decimals=2))
                edit.textChanged.connect(self.changed.emit)
                self.fields[field] = edit
                form.addRow(field.replace("_", " ").title(), edit)
            section_layout.addWidget(label)
            section_layout.addLayout(form)
            body_layout.addWidget(section)
        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def load_data(self, data: dict[str, Any]) -> None:
        """Populate form fields from extracted invoice data."""
        for name, edit in self.fields.items():
            edit.blockSignals(True)
            value = data.get(name)
            edit.setText("" if value is None else str(value))
            edit.blockSignals(False)

    def is_valid(self) -> bool:
        """Return True when all validator-backed fields are acceptable."""
        for edit in self.fields.values():
            if edit.validator() and not edit.hasAcceptableInput() and edit.text().strip():
                edit.setObjectName("invalidInput")
                return False
            edit.setObjectName("")
        return True

    def values(self) -> dict[str, Any]:
        """Return the current form values with backend-compatible casting."""
        return {name: cast_field(name, edit.text()) for name, edit in self.fields.items()}


class LineItemsTable(QWidget):
    """Editable line-items grid for invoice product/service rows."""

    changed = Signal()

    def __init__(self) -> None:
        """Build line item controls and table."""
        super().__init__()
        self.loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        actions = QHBoxLayout()
        add = QPushButton("Add Line")
        remove = QPushButton("Remove Line")
        add.clicked.connect(self.add_line)
        remove.clicked.connect(self.remove_line)
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addStretch()
        self.table = QTableWidget(0, len(LINE_COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _, label in LINE_COLUMNS])
        self.table.setItemDelegate(LineItemDelegate(self.table))
        self.table.itemChanged.connect(self.item_changed)
        layout.addLayout(actions)
        layout.addWidget(self.table)

    def load_items(self, items: list[dict[str, Any]]) -> None:
        """Populate the table from extracted line item data."""
        self.loading = True
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            flat_item = flatten_line_item_taxes(item)
            for col, (name, _label) in enumerate(LINE_COLUMNS):
                value = flat_item.get(name)
                cell = QTableWidgetItem("" if value is None else str(value))
                self.table.setItem(row, col, cell)
        self.loading = False

    def add_line(self) -> None:
        """Append a blank line item row."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, (name, _label) in enumerate(LINE_COLUMNS):
            self.table.setItem(row, col, QTableWidgetItem(str(row + 1) if name == "sr_no" else ""))
        self.changed.emit()

    def remove_line(self) -> None:
        """Remove the currently selected line item row."""
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.changed.emit()

    def item_changed(self, item: QTableWidgetItem) -> None:
        """Recalculate totals when editable numeric cells change."""
        if self.loading:
            return
        self.validate_item(item)
        name = LINE_COLUMNS[item.column()][0]
        if name in {"quantity", "rate", "discount", "cess_amount", *TAX_RATE_FIELDS, *TAX_AMOUNT_FIELDS}:
            self.recalculate_row(item.row())
        self.changed.emit()

    def recalculate_row(self, row: int) -> None:
        """Recalculate taxable value and total for a row."""
        try:
            values = self.row_values(row)
        except ValueError:
            self.validate_row(row)
            return
        taxable = calculate_taxable_value(values)
        self.set_cell(row, "taxable_value", f"{taxable:.2f}")
        for component in TAX_COMPONENTS:
            rate = values.get(f"{component}_rate") or 0.0
            amount = round(taxable * rate / 100, 2)
            self.set_cell(row, f"{component}_amount", f"{amount:.2f}")
            values[f"{component}_amount"] = amount
        total = calculate_line_total({**values, "taxable_value": taxable})
        self.set_cell(row, "total", f"{total:.2f}")

    def set_cell(self, row: int, name: str, value: str) -> None:
        """Update one table cell without recursively triggering recalculation."""
        self.loading = True
        col = [column[0] for column in LINE_COLUMNS].index(name)
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, col, item)
        item.setText(value)
        self.validate_item(item)
        self.loading = False

    def is_valid(self) -> bool:
        """Return True when table numeric values can be parsed."""
        valid = True
        for row in range(self.table.rowCount()):
            valid = self.validate_row(row) and valid
        return valid

    def validate_row(self, row: int) -> bool:
        """Validate one row and mark invalid numeric cells."""
        valid = True
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item is not None:
                valid = self.validate_item(item) and valid
        return valid

    def validate_item(self, item: QTableWidgetItem) -> bool:
        """Validate one table item and update its background color."""
        name = LINE_COLUMNS[item.column()][0]
        try:
            cast_line_field(name, item.text().strip())
        except ValueError:
            item.setBackground(INVALID_CELL_COLOR)
            return False
        item.setBackground(VALID_CELL_COLOR)
        return True

    def row_values(self, row: int) -> dict[str, Any]:
        """Return one table row as a typed line item dictionary."""
        values = {}
        for col, (name, _label) in enumerate(LINE_COLUMNS):
            item = self.table.item(row, col)
            values[name] = cast_line_field(name, item.text().strip() if item else "")
        return values

    def values(self) -> list[dict[str, Any]]:
        """Return all line items as typed dictionaries."""
        return [build_line_item_taxes(self.row_values(row)) for row in range(self.table.rowCount())]


class DetailPage(QWidget):
    """Invoice detail/review page with metadata, lines, validation, audit, and PDF."""

    back_requested = Signal()
    audit_requested = Signal(int)
    approve_requested = Signal(int)
    corrections_requested = Signal(int, dict)
    reject_requested = Signal(int, str)
    reprocess_requested = Signal(int)
    export_requested = Signal(int, str)
    pdf_requested = Signal(int)

    def __init__(self) -> None:
        """Build the split detail page and footer review actions."""
        super().__init__()
        self.invoice: dict[str, Any] | None = None
        self.original_data: dict[str, Any] = {}
        self.dirty = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        header = QHBoxLayout()
        back = QPushButton("Back")
        back.clicked.connect(self.back_requested.emit)
        self.title = QLabel("Invoice Detail")
        self.title.setObjectName("pageTitle")
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        header.addWidget(back)
        header.addWidget(self.title)
        header.addWidget(self.summary)
        header.addStretch()
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tabs = QTabWidget()
        self.metadata = MetadataForm()
        self.line_items = LineItemsTable()
        self.validation = ValidationPane()
        self.audit = AuditPane()
        self.metadata.changed.connect(self.mark_dirty)
        self.line_items.changed.connect(self.mark_dirty)
        self.audit.load_requested.connect(self.request_audit)
        self.tabs.addTab(self.metadata, "Metadata")
        self.tabs.addTab(self.line_items, "Line Items")
        self.tabs.addTab(self.validation, "Validation")
        self.tabs.addTab(self.audit, "Audit Logs")
        self.tabs.currentChanged.connect(self.tab_changed)

        pdf_panel = QFrame()
        pdf_panel.setObjectName("pdfPanel")
        pdf_layout = QVBoxLayout(pdf_panel)
        pdf_header = QLabel("Invoice Document")
        pdf_header.setObjectName("sectionTitle")
        self.pdf_preview = PdfPreview()
        pdf_layout.addWidget(pdf_header)
        pdf_layout.addWidget(self.pdf_preview)

        splitter.addWidget(self.tabs)
        splitter.addWidget(pdf_panel)
        splitter.setSizes([560, 720])
        layout.addWidget(splitter, stretch=1)

        footer = QHBoxLayout()
        self.approve_btn = QPushButton("Approve")
        self.corrections_btn = QPushButton("Submit Corrections")
        self.reject_btn = QPushButton("Reject")
        self.reprocess_btn = QPushButton("Reprocess")
        self.export_btn = QToolButton()
        self.export_btn.setText("Export Data")
        self.export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.export_btn)
        for fmt, label in [("csv", "CSV"), ("json", "JSON"), ("tally", "Tally XML"), ("erpnext", "ERPNext")]:
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, f=fmt: self.request_export(f))
            menu.addAction(action)
        self.export_btn.setMenu(menu)
        self.approve_btn.clicked.connect(self.request_approve)
        self.corrections_btn.clicked.connect(self.request_corrections)
        self.reject_btn.clicked.connect(self.request_reject)
        self.reprocess_btn.clicked.connect(self.request_reprocess)
        footer.addStretch()
        for button in (self.approve_btn, self.corrections_btn, self.reject_btn, self.reprocess_btn, self.export_btn):
            footer.addWidget(button)
        layout.addLayout(footer)

    def load_invoice(self, invoice: dict[str, Any]) -> None:
        """Populate the detail page from an invoice record."""
        self.invoice = invoice
        self.original_data = copy.deepcopy(invoice.get("extracted_data") or {})
        confidence = invoice.get("confidence_score")
        self.title.setText(f"Invoice #{invoice.get('id')} - {invoice.get('status')}")
        self.summary.setText(f"Confidence: {float(confidence) * 100:.0f}%" if confidence is not None else "")
        self.metadata.load_data(self.original_data)
        self.line_items.load_items(self.original_data.get("line_items") or [])
        self.validation.set_validation(invoice.get("validation"))
        self.audit.set_logs([])
        self.dirty = False
        self.sync_actions()
        self.pdf_requested.emit(int(invoice["id"]))

    def set_pdf_loading(self, pdf_path) -> None:
        """Show PDF loading state."""
        self.pdf_preview.set_loading(pdf_path)

    def set_pdf_pages(self, image_paths: list) -> None:
        """Render completed PDF preview pages."""
        self.pdf_preview.set_pages(image_paths)

    def set_pdf_error(self, message: str) -> None:
        """Render PDF preview failure."""
        self.pdf_preview.set_error(message)

    def mark_dirty(self) -> None:
        """Mark the invoice form as edited."""
        self.dirty = True
        self.sync_actions()

    def sync_actions(self) -> None:
        """Enable or disable review actions based on invoice state."""
        status = (self.invoice or {}).get("status")
        reviewable = status in {"Pending_Review", "Rejected", "Extracted"}
        valid = self.metadata.is_valid() and self.line_items.is_valid()
        self.approve_btn.setEnabled(reviewable and valid)
        self.reject_btn.setEnabled(reviewable)
        self.corrections_btn.setEnabled(reviewable and self.dirty and valid)

    def invoice_id(self) -> int | None:
        """Return the current invoice ID, if a record is loaded."""
        return int(self.invoice["id"]) if self.invoice else None

    def build_corrections(self) -> dict[str, Any]:
        """Return only changed metadata and line item values."""
        current = self.metadata.values()
        current["line_items"] = self.line_items.values()
        return {key: value for key, value in current.items() if value != self.original_data.get(key)}

    def request_audit(self) -> None:
        """Request audit logs for the current invoice."""
        if self.invoice_id() is not None:
            self.audit.set_loading()
            self.audit_requested.emit(self.invoice_id())

    def tab_changed(self, index: int) -> None:
        """Auto-load audit logs when the audit tab is selected."""
        if self.tabs.widget(index) is self.audit:
            self.request_audit()

    def request_approve(self) -> None:
        """Emit an approve request for the current invoice."""
        if self.invoice_id() is not None and QMessageBox.question(self, "Approve Invoice", "Approve this invoice?") == QMessageBox.StandardButton.Yes:
            self.approve_requested.emit(self.invoice_id())

    def request_corrections(self) -> None:
        """Emit corrections for changed fields and line items."""
        if self.invoice_id() is not None:
            corrections = self.build_corrections()
            if corrections:
                self.corrections_requested.emit(self.invoice_id(), corrections)

    def request_reject(self) -> None:
        """Prompt for a rejection reason and emit the rejection request."""
        if self.invoice_id() is None:
            return
        reason, ok = QInputDialog.getMultiLineText(self, "Reject Invoice", "Rejection reason")
        if ok and reason.strip():
            self.reject_requested.emit(self.invoice_id(), reason.strip())

    def request_reprocess(self) -> None:
        """Confirm and emit an invoice reprocess request."""
        if self.invoice_id() is not None and QMessageBox.question(self, "Reprocess Invoice", "Run extraction again?") == QMessageBox.StandardButton.Yes:
            self.reprocess_requested.emit(self.invoice_id())

    def request_export(self, fmt: str) -> None:
        """Emit an export request for the selected format."""
        if self.invoice_id() is not None:
            self.export_requested.emit(self.invoice_id(), fmt)


def cast_field(name: str, value: str) -> Any:
    """Cast metadata form text into values accepted by invoice schemas."""
    text = value.strip()
    if text == "":
        return None
    if name in NUMERIC_FIELDS:
        return float(text)
    return text


def flatten_line_item_taxes(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested tax rows into editable GST component columns."""
    flattened = dict(item)
    for component in TAX_COMPONENTS:
        flattened.setdefault(f"{component}_rate", 0.0)
        flattened.setdefault(f"{component}_amount", 0.0)
    for tax in item.get("taxes") or []:
        tax_type = str(tax.get("tax_type") or "").strip().lower()
        if tax_type in TAX_COMPONENTS:
            flattened[f"{tax_type}_rate"] = tax.get("tax_rate") or 0.0
            flattened[f"{tax_type}_amount"] = (flattened.get(f"{tax_type}_amount") or 0.0) + (tax.get("tax_amount") or 0.0)
    if flattened.get("taxable_value") is not None:
        flattened["total"] = calculate_line_total(flattened)
    return flattened


def build_line_item_taxes(values: dict[str, Any]) -> dict[str, Any]:
    """Rebuild nested GST tax rows from editable component columns."""
    item = {
        key: value
        for key, value in values.items()
        if key not in TAX_RATE_FIELDS and key not in TAX_AMOUNT_FIELDS
    }
    taxable_value = values.get("taxable_value") or 0.0
    taxes = []
    for component in TAX_COMPONENTS:
        rate = values.get(f"{component}_rate") or 0.0
        amount = values.get(f"{component}_amount") or 0.0
        if rate > 0 or amount > 0:
            taxes.append({
                "tax_type": component.upper(),
                "tax_rate": rate,
                "taxable_amount": taxable_value,
                "tax_amount": amount,
            })
    item["taxes"] = taxes
    item["total"] = calculate_line_total(values)
    return item


def calculate_taxable_value(values: dict[str, Any]) -> float:
    """Calculate taxable value from quantity, rate, and discount."""
    return max((values.get("quantity") or 0.0) * (values.get("rate") or 0.0) - (values.get("discount") or 0.0), 0.0)


def calculate_line_total(values: dict[str, Any]) -> float:
    """Calculate gross line total including GST components and cess."""
    tax_total = sum(values.get(f"{component}_amount") or 0.0 for component in TAX_COMPONENTS)
    return round((values.get("taxable_value") or 0.0) + tax_total + (values.get("cess_amount") or 0.0), 2)


def cast_line_field(name: str, value: str) -> Any:
    """Cast a line-item table cell into the correct schema value."""
    text = value.strip()
    if text == "":
        return 0.0 if name in LINE_FLOAT_FIELDS else None
    if name == "sr_no":
        return int(text)
    if name in LINE_FLOAT_FIELDS:
        return float(text)
    return text


class LineItemDelegate(QStyledItemDelegate):
    """Table editor delegate that restricts line item numeric columns."""

    def createEditor(self, parent, option, index: QModelIndex):  # type: ignore[override]
        """Create a validated editor for numeric table columns."""
        editor = super().createEditor(parent, option, index)
        name = LINE_COLUMNS[index.column()][0]
        if isinstance(editor, QLineEdit):
            if name in LINE_INT_FIELDS:
                editor.setValidator(QIntValidator(0, 999999, editor))
            elif name in LINE_FLOAT_FIELDS:
                validator = QDoubleValidator(-999999999.0, 999999999.0, 2, editor)
                validator.setNotation(QDoubleValidator.Notation.StandardNotation)
                editor.setValidator(validator)
        return editor
