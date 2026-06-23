from __future__ import annotations

"""Invoice detail and human review page."""

import copy
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QDoubleValidator, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .constants import COLLAPSIBLE_FIELD_GROUPS, EXPORT_ACTIONS, FIELD_GROUPS, NUMERIC_FIELDS, REQUIRED_METADATA_FIELDS
from .widgets.audit_pane import AuditPane
from .widgets.line_items_table import LineItemsTable
from .widgets.pdf_preview import PdfPreview
from .widgets.tally_mappings_table import TallyMappingsTable
from .widgets.validation_pane import ValidationPane


class CollapsibleSection(QFrame):
    """Compact titled section with optional collapsed content."""

    def __init__(self, title: str, *, collapsible: bool = False, expanded: bool = True) -> None:
        super().__init__()
        self.title = title
        self.collapsible = collapsible
        self.setObjectName("subsection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setObjectName("sectionToggle")
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setText(title)
        self.toggle.setCheckable(collapsible)
        self.toggle.setChecked(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.toggled.connect(self.set_expanded)
        if not collapsible:
            self.toggle.setArrowType(Qt.ArrowType.NoArrow)
        header.addWidget(self.toggle)
        header.addStretch()
        layout.addLayout(header)

        self.hint = QLabel("")
        self.hint.setObjectName("requiredHint")
        self.hint.setVisible(False)
        layout.addWidget(self.hint)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        layout.addWidget(self.content)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide the section content."""
        self.content.setVisible(expanded)
        if self.collapsible:
            self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def set_missing_hint(self, missing: list[str]) -> None:
        """Show a compact list of missing required fields in this section."""
        if missing:
            self.hint.setText("Required: " + ", ".join(missing))
            self.hint.setVisible(True)
        else:
            self.hint.clear()
            self.hint.setVisible(False)

    def set_collapsed(self, collapsed: bool) -> None:
        """Set collapse state for collapsible sections."""
        if not self.collapsible:
            return
        self.toggle.setChecked(not collapsed)
        self.set_expanded(not collapsed)


class MetadataForm(QWidget):
    """Editable invoice metadata form grouped by business section."""

    changed = Signal()

    def __init__(self, line_items: LineItemsTable, tally_mappings: TallyMappingsTable) -> None:
        """Build all metadata fields and group containers."""
        super().__init__()
        self.fields: dict[str, QLineEdit] = {}
        self.labels: dict[str, QLabel] = {}
        self.sections: dict[str, CollapsibleSection] = {}
        self.line_items = line_items
        self.tally_mappings = tally_mappings
        self.line_items_section: CollapsibleSection | None = None
        self.tally_mappings_section: CollapsibleSection | None = None
        
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setSpacing(8)

        self.add_field_section(body_layout, "Voucher Details", FIELD_GROUPS["Voucher Details"])
        party_row = QHBoxLayout()
        party_row.setSpacing(8)
        party_row.addWidget(self.build_field_section("Vendor / Party Details", FIELD_GROUPS["Vendor / Party Details"]))
        party_row.addWidget(self.build_field_section("Customer / Buyer Details", FIELD_GROUPS["Customer / Buyer Details"]))
        body_layout.addLayout(party_row)

        self.line_items_section = CollapsibleSection("Line Items", collapsible=True, expanded=True)
        self.line_items_section.content_layout.addWidget(self.line_items)
        body_layout.addWidget(self.line_items_section)

        self.tally_mappings_section = CollapsibleSection("Tally Mapping", collapsible=True, expanded=True)
        self.tally_mappings_section.content_layout.addWidget(self.tally_mappings)
        body_layout.addWidget(self.tally_mappings_section)

        # Move Tax & Totals immediately after Tally Mapping section
        self.add_field_section(body_layout, "Tax & Totals", FIELD_GROUPS["Tax & Totals"])

        for group in ("Shipping & Transport", "Bank Details"):
            self.add_field_section(
                body_layout,
                group,
                FIELD_GROUPS[group],
                collapsible=group in COLLAPSIBLE_FIELD_GROUPS,
                expanded=group not in COLLAPSIBLE_FIELD_GROUPS,
            )
        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def add_field_section(
        self,
        layout: QVBoxLayout,
        title: str,
        fields: list[str],
        *,
        collapsible: bool = False,
        expanded: bool = True,
    ) -> None:
        """Build and add one metadata field section."""
        layout.addWidget(self.build_field_section(title, fields, collapsible=collapsible, expanded=expanded))

    def build_field_section(
        self,
        title: str,
        fields: list[str],
        *,
        collapsible: bool = False,
        expanded: bool = True,
    ) -> CollapsibleSection:
        """Return one compact two-column metadata section with left-packed inputs."""
        section = CollapsibleSection(title, collapsible=collapsible, expanded=expanded)
        self.sections[title] = section
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for index, field in enumerate(fields):
            row = index // 2
            offset = (index % 2) * 2
            label = QLabel(self.field_label(field))
            label.setObjectName("requiredLabel" if field in REQUIRED_METADATA_FIELDS else "fieldLabel")
            edit = QLineEdit()
            edit.setMinimumWidth(150)
            edit.setMaximumWidth(220)
            if field in NUMERIC_FIELDS:
                edit.setValidator(QDoubleValidator(bottom=-999999999.0, top=999999999.0, decimals=2))
            edit.textChanged.connect(lambda _text, signal=self.changed: signal.emit())
            edit.textChanged.connect(self.update_required_state)
            self.fields[field] = edit
            self.labels[field] = label
            grid.addWidget(label, row, offset)
            grid.addWidget(edit, row, offset + 1)
        grid.setColumnStretch(4, 1)
        section.content_layout.addLayout(grid)
        return section

    def field_label(self, field: str) -> str:
        """Return a reviewer-friendly field label."""
        label = field.replace("_", " ").title()
        return f"{label} *" if field in REQUIRED_METADATA_FIELDS else label

    def load_data(self, data: dict[str, Any]) -> None:
        """Populate form fields from extracted invoice data."""
        for name, edit in self.fields.items():
            edit.blockSignals(True)
            value = data.get(name)
            edit.setText("" if value is None else str(value))
            edit.blockSignals(False)
        self.update_optional_collapses(data)
        self.update_required_state()

    def update_optional_collapses(self, data: dict[str, Any]) -> None:
        """Collapse optional sections by default when loading data."""
        for group in COLLAPSIBLE_FIELD_GROUPS:
            section = self.sections.get(group)
            if section:
                section.set_collapsed(True)

    def set_line_item_count(self, count: int) -> None:
        """Update required state for the embedded line-item section."""
        if self.line_items_section:
            self.line_items_section.set_missing_hint([] if count else ["At least one line item"])

    def update_required_state(self, *_args) -> None:
        """Highlight empty export-essential fields without blocking review actions."""
        missing_by_section: dict[str, list[str]] = {title: [] for title in self.sections}
        for field in REQUIRED_METADATA_FIELDS:
            edit = self.fields.get(field)
            label = self.labels.get(field)
            if edit is None or label is None:
                continue
            missing = not edit.text().strip()
            if missing:
                self.set_object_name(edit, "requiredMissing")
            elif edit.objectName() == "requiredMissing":
                self.set_object_name(edit, "")
            self.set_object_name(label, "requiredMissingLabel" if missing else "requiredLabel")
            if missing:
                section_name = self.section_for_field(field)
                missing_by_section.setdefault(section_name, []).append(field.replace("_", " ").title())
        for section_name, missing in missing_by_section.items():
            section = self.sections.get(section_name)
            if section:
                section.set_missing_hint(missing)

    def section_for_field(self, field: str) -> str:
        """Return the section title containing a field."""
        for title, fields in FIELD_GROUPS.items():
            if field in fields:
                return title
        return "Voucher Details"

    def set_object_name(self, widget: QWidget, name: str) -> None:
        """Update QSS object name and force the widget to refresh styling."""
        if widget.objectName() == name:
            return
        widget.setObjectName(name)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def is_valid(self) -> bool:
        """Return True when all validator-backed fields are acceptable."""
        valid = True
        for edit in self.fields.values():
            if edit.validator() and not edit.hasAcceptableInput() and edit.text().strip():
                self.set_object_name(edit, "invalidInput")
                valid = False
            elif edit.objectName() == "invalidInput":
                self.set_object_name(edit, "")
        self.update_required_state()
        return valid

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
        self.original_mappings: list[dict[str, Any]] = []
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

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter = self.splitter
        self.tabs = QTabWidget()
        self.line_items = LineItemsTable()
        self.tally_mappings = TallyMappingsTable()
        self.metadata = MetadataForm(self.line_items, self.tally_mappings)
        self.validation = ValidationPane()
        self.audit = AuditPane()

        # Raw Markdown comparison view
        self.raw_text_pane = QTextEdit()
        self.raw_text_pane.setReadOnly(True)
        self.raw_text_pane.setObjectName("rawTextPane")

        self.metadata.changed.connect(self.mark_dirty)
        self.line_items.changed.connect(self.mark_dirty)
        self.tally_mappings.changed.connect(self.mark_dirty)
        self.line_items.changed.connect(lambda: self.metadata.set_line_item_count(self.line_items.table.rowCount()))
        self.audit.load_requested.connect(self.request_audit)

        self.tabs.addTab(self.metadata, "Metadata")
        self.tabs.addTab(self.validation, "Validation")
        self.tabs.addTab(self.audit, "Audit Logs")
        self.tabs.addTab(self.raw_text_pane, "Raw Markdown")
        self.tabs.currentChanged.connect(self.tab_changed)

        pdf_panel = QFrame()
        pdf_panel.setObjectName("pdfPanel")
        pdf_panel.setMinimumWidth(360)
        pdf_layout = QVBoxLayout(pdf_panel)
        pdf_header = QLabel("Invoice Document")
        pdf_header.setObjectName("sectionTitle")
        self.pdf_preview = PdfPreview()
        pdf_layout.addWidget(pdf_header)
        pdf_layout.addWidget(self.pdf_preview)

        splitter.addWidget(self.tabs)
        splitter.addWidget(pdf_panel)
        splitter.setSizes([600, 400])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        footer = QHBoxLayout()

        # Buttons with hotkeys / shortcuts
        self.approve_btn = QPushButton("Approve (Alt+A)")
        self.approve_btn.setShortcut(QKeySequence("Alt+A"))

        self.corrections_btn = QPushButton("Submit Corrections (Alt+C)")
        self.corrections_btn.setShortcut(QKeySequence("Alt+C"))

        self.reject_btn = QPushButton("Reject (Alt+R)")
        self.reject_btn.setShortcut(QKeySequence("Alt+R"))

        self.reprocess_btn = QPushButton("Reprocess (Alt+P)")
        self.reprocess_btn.setShortcut(QKeySequence("Alt+P"))

        self.export_btn = QToolButton()
        self.export_btn.setText("Export Data")
        self.export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.export_btn)
        for fmt, label in EXPORT_ACTIONS:
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
        self.original_mappings = copy.deepcopy(invoice.get("tally_mappings") or [])
        confidence = invoice.get("confidence_score")
        self.title.setText(f"Invoice #{invoice.get('id')} - {invoice.get('status')}")
        self.summary.setText(f"Confidence: {float(confidence) * 100:.0f}%" if confidence is not None else "")

        # Load raw markdown if present
        self.raw_text_pane.setText(invoice.get("raw_markdown") or "No raw markdown available.")

        self.metadata.load_data(self.original_data)
        self.line_items.load_items(self.original_data.get("line_items") or [])
        self.tally_mappings.load_mappings(self.original_mappings)
        self.metadata.set_line_item_count(self.line_items.table.rowCount())
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
        exportable = status in {"Approved", "Posted"}
        valid = self.metadata.is_valid() and self.line_items.is_valid()
        self.approve_btn.setEnabled(reviewable and valid)
        self.reject_btn.setEnabled(reviewable)
        self.corrections_btn.setEnabled(self.invoice is not None and self.dirty and valid)
        self.export_btn.setEnabled(exportable)

    def invoice_id(self) -> int | None:
        """Return the current invoice ID, if a record is loaded."""
        return int(self.invoice["id"]) if self.invoice else None

    def build_corrections(self) -> dict[str, Any]:
        """Return only changed metadata and line item values."""
        current = self.metadata.values()
        current["line_items"] = self.line_items.values()
        corrections = {key: value for key, value in current.items() if value != self.original_data.get(key)}
        changed_mappings = self.tally_mappings.changed_values()
        if changed_mappings:
            corrections["tally_mappings"] = changed_mappings
        return corrections

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
