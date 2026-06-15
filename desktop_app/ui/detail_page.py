from __future__ import annotations

"""Invoice detail and human review page."""

import copy
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QDoubleValidator
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
    QTabWidget,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .constants import FIELD_GROUPS, NUMERIC_FIELDS
from .widgets.audit_pane import AuditPane
from .widgets.line_items_table import LineItemsTable, build_line_item_taxes, cast_line_field, flatten_line_item_taxes
from .widgets.pdf_preview import PdfPreview
from .widgets.validation_pane import ValidationPane


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
        for fmt, label in [("csv", "CSV"), ("json", "JSON"), ("tally", "Tally XML"), ("tally_post", "Post to TallyPrime"), ("tally_vendor", "Sync Vendor Master"), ("tally_ledgers", "Sync GST Ledgers"), ("erpnext", "ERPNext")]:
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
