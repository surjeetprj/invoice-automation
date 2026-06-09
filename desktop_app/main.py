from __future__ import annotations

"""PySide6 desktop UI for the self-contained Invoice AI application.

This module owns the visual shell, page widgets, background worker plumbing,
PDF preview rendering, and all signal wiring between the UI and the local
desktop workflow service.
"""

import copy
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QInputDialog,
)

from config import LOG_DIR
from services.workflow import DesktopWorkflow


STATUS_COLORS = {
    "Approved": "#059669",
    "Rejected": "#dc2626",
    "Pending_Review": "#d97706",
    "Extracted": "#7c3aed",
    "New": "#2563eb",
    "In_Process": "#0891b2",
    "Posted": "#6d28d9",
}

NUMERIC_FIELDS = {
    "total_taxable_amount", "total_cgst", "total_sgst", "total_igst",
    "total_cess", "total_tax_amount", "round_off", "total_amount",
}
LINE_FLOAT_FIELDS = {"quantity", "rate", "discount", "taxable_value", "cess_amount", "total"}

FIELD_GROUPS = {
    "General": ["invoice_number", "date", "due_date", "place_of_supply", "amount_in_words"],
    "Vendor": ["vendor_name", "vendor_address", "vendor_gstin", "vendor_state_code", "vendor_pan", "vendor_msme_no", "vendor_contact"],
    "Customer": ["customer_name", "customer_address", "customer_gstin", "customer_state_code", "customer_pan", "customer_phone"],
    "Shipping & Transport": ["shipping_name", "shipping_address", "shipping_gstin", "transport_name", "transport_id", "vehicle_number", "challan_no", "challan_date", "e_way_bill_no", "irn", "ack_number", "ack_date"],
    "Tax & Totals": ["total_taxable_amount", "total_cgst", "total_sgst", "total_igst", "total_cess", "total_tax_amount", "round_off", "total_amount"],
    "Bank": ["bank_name", "account_no", "ifsc", "branch"],
}

LINE_COLUMNS = [
    ("sr_no", "Sr No"), ("description", "Description"), ("hsn_sac", "HSN/SAC"),
    ("unit", "Unit"), ("quantity", "Quantity"), ("rate", "Rate"),
    ("taxable_value", "Taxable"), ("cess_amount", "Cess"), ("discount", "Discount"),
    ("total", "Total"),
]


class WorkerSignals(QObject):
    """Qt signals emitted by a background worker."""

    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    """Run a blocking callable on Qt's thread pool and report its result."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Store the callable and arguments for later background execution."""
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        """Execute the task and emit result, error, and completion signals."""
        try:
            self.signals.result.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class DropZone(QFrame):
    """Drag-and-drop PDF upload area."""

    file_dropped = Signal(str)

    def __init__(self) -> None:
        """Create the upload dropzone and file picker button."""
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Drop PDF invoice here")
        title.setObjectName("dropTitle")
        hint = QLabel("or choose a system-generated PDF under 10 MB")
        hint.setObjectName("muted")
        button = QPushButton("Choose PDF")
        button.clicked.connect(self.choose_file)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        """Accept drag events only for local PDF files."""
        if event.mimeData().hasUrls() and Path(event.mimeData().urls()[0].toLocalFile()).suffix.lower() == ".pdf":
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        """Emit the dropped local file path."""
        self.file_dropped.emit(event.mimeData().urls()[0].toLocalFile())

    def choose_file(self) -> None:
        """Open a file dialog and emit the selected PDF path."""
        path, _ = QFileDialog.getOpenFileName(self, "Select invoice PDF", "", "PDF files (*.pdf)")
        if path:
            self.file_dropped.emit(path)


class DashboardPage(QWidget):
    """Dashboard page showing KPI cards and status distribution."""

    refresh_requested = Signal()

    def __init__(self) -> None:
        """Build the dashboard page layout."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(20)
        header = QHBoxLayout()
        title = QLabel("Dashboard")
        title.setObjectName("pageTitle")
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(refresh)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards: dict[str, QLabel] = {}
        for index, label in enumerate(["Total Invoices", "Avg Processing Time", "Accuracy Rate", "Pending Review"]):
            card = QFrame()
            card.setObjectName("card")
            body = QVBoxLayout(card)
            caption = QLabel(label)
            caption.setObjectName("muted")
            value = QLabel("--")
            value.setObjectName("kpi")
            self.cards[label] = value
            body.addWidget(caption)
            body.addWidget(value)
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        section = QLabel("Status Distribution")
        section.setObjectName("sectionTitle")
        self.status_body = QVBoxLayout()
        layout.addWidget(section)
        layout.addLayout(self.status_body)
        layout.addStretch()

    def set_stats(self, stats: dict[str, Any]) -> None:
        """Render dashboard statistics returned by the workflow service."""
        total = stats.get("total_invoices") or 0
        avg_ms = stats.get("avg_processing_time_ms")
        approved = stats.get("total_approved") or 0
        accuracy = round((approved / total) * 100) if total else 0
        self.cards["Total Invoices"].setText(str(total))
        self.cards["Avg Processing Time"].setText("--" if avg_ms is None else f"{float(avg_ms) / 1000:.2f}s")
        self.cards["Accuracy Rate"].setText(f"{accuracy}%")
        self.cards["Pending Review"].setText(str(stats.get("total_pending_review") or 0))
        clear_layout(self.status_body)
        distribution = stats.get("status_distribution") or {}
        if not distribution:
            self.status_body.addWidget(QLabel("No invoices processed yet."))
            return
        bar = QFrame()
        bar.setObjectName("statusBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        for status, count in distribution.items():
            segment = QFrame()
            segment.setToolTip(f"{status}: {count}")
            segment.setStyleSheet(f"background: {STATUS_COLORS.get(status, '#64748b')};")
            row.addWidget(segment, int(count))
        self.status_body.addWidget(bar)
        legend = QHBoxLayout()
        for status, count in distribution.items():
            label = QLabel(f"{status.replace('_', ' ')}: {count}")
            label.setStyleSheet(f"color: {STATUS_COLORS.get(status, '#475569')}; font-weight: 600;")
            legend.addWidget(label)
        legend.addStretch()
        self.status_body.addLayout(legend)


class UploadPage(QWidget):
    """Invoice upload page with dropzone and processing indicator."""

    upload_requested = Signal(str)

    def __init__(self) -> None:
        """Build the upload page."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        title = QLabel("Upload Invoice")
        title.setObjectName("pageTitle")
        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self.upload_requested.emit)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(self.drop_zone, stretch=1)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

    def set_busy(self, busy: bool, message: str = "") -> None:
        """Toggle upload progress state and status text."""
        self.progress.setVisible(busy)
        self.status.setText(message)
        self.drop_zone.setEnabled(not busy)


class InvoicesPage(QWidget):
    """Invoice list page with local search and row selection."""

    refresh_requested = Signal()
    invoice_selected = Signal(int)

    def __init__(self) -> None:
        """Build the invoice table and search controls."""
        super().__init__()
        self.all_invoices: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        header = QHBoxLayout()
        title = QLabel("Invoices")
        title.setObjectName("pageTitle")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search vendor or invoice number")
        self.search.textChanged.connect(self.apply_filter)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.search, stretch=1)
        header.addWidget(refresh)
        layout.addLayout(header)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "Vendor", "Invoice No", "Date", "Total Amount", "Confidence", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.open_row)
        layout.addWidget(self.table)

    def set_invoices(self, payload: dict[str, Any]) -> None:
        """Load invoice records into the table model."""
        self.all_invoices = payload.get("invoices", [])
        self.apply_filter()

    def apply_filter(self) -> None:
        """Filter visible rows by vendor name or invoice number."""
        query = self.search.text().strip().lower()
        rows = []
        for invoice in self.all_invoices:
            data = invoice.get("extracted_data") or {}
            haystack = f"{data.get('vendor_name') or invoice.get('filename') or ''} {data.get('invoice_number') or ''}".lower()
            if not query or query in haystack:
                rows.append(invoice)
        self.table.setRowCount(len(rows))
        for row, invoice in enumerate(rows):
            data = invoice.get("extracted_data") or {}
            values = [
                invoice.get("id", ""),
                data.get("vendor_name") or invoice.get("filename") or "",
                data.get("invoice_number") or "",
                data.get("date") or "",
                format_money(data.get("total_amount")),
                format_confidence(invoice.get("confidence_score") or data.get("confidence_score")),
                invoice.get("status") or "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, invoice.get("id"))
                if col == 6:
                    item.setForeground(QColor(STATUS_COLORS.get(str(value), "#334155")))
                self.table.setItem(row, col, item)

    def open_row(self, item: QTableWidgetItem) -> None:
        """Emit the selected invoice ID when a row is opened."""
        invoice_id = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if invoice_id is not None:
            self.invoice_selected.emit(int(invoice_id))


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
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.item_changed)
        layout.addLayout(actions)
        layout.addWidget(self.table)

    def load_items(self, items: list[dict[str, Any]]) -> None:
        """Populate the table from extracted line item data."""
        self.loading = True
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            for col, (name, _label) in enumerate(LINE_COLUMNS):
                value = item.get(name)
                self.table.setItem(row, col, QTableWidgetItem("" if value is None else str(value)))
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
        if LINE_COLUMNS[item.column()][0] in {"quantity", "rate", "discount", "cess_amount"}:
            self.recalculate_row(item.row())
        self.changed.emit()

    def recalculate_row(self, row: int) -> None:
        """Recalculate taxable value and total for a row."""
        values = self.row_values(row)
        taxable = max((values.get("quantity") or 0.0) * (values.get("rate") or 0.0) - (values.get("discount") or 0.0), 0.0)
        total = taxable + (values.get("cess_amount") or 0.0)
        self.set_cell(row, "taxable_value", f"{taxable:.2f}")
        self.set_cell(row, "total", f"{total:.2f}")

    def set_cell(self, row: int, name: str, value: str) -> None:
        """Update one table cell without recursively triggering recalculation."""
        self.loading = True
        col = [column[0] for column in LINE_COLUMNS].index(name)
        item = self.table.item(row, col) or QTableWidgetItem()
        item.setText(value)
        self.table.setItem(row, col, item)
        self.loading = False

    def row_values(self, row: int) -> dict[str, Any]:
        """Return one table row as a typed line item dictionary."""
        values = {}
        for col, (name, _label) in enumerate(LINE_COLUMNS):
            item = self.table.item(row, col)
            values[name] = cast_line_field(name, item.text().strip() if item else "")
        return values

    def values(self) -> list[dict[str, Any]]:
        """Return all line items as typed dictionaries."""
        return [self.row_values(row) for row in range(self.table.rowCount())]


class ValidationPane(QWidget):
    """Validation warnings/errors tab."""

    def __init__(self) -> None:
        """Create the validation issues container."""
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def set_validation(self, validation: dict[str, Any] | None) -> None:
        """Render validation issues for the current invoice."""
        clear_layout(self.layout)
        issues = (validation or {}).get("issues") or []
        if not issues:
            self.layout.addWidget(QLabel("No validation issues reported."))
            return
        for issue in issues:
            label = QLabel(f"{issue.get('field', 'General')}: {issue.get('message', '')}")
            label.setWordWrap(True)
            label.setObjectName("errorBanner" if issue.get("severity") == "error" else "warningBanner")
            self.layout.addWidget(label)


class AuditPane(QWidget):
    """Audit log tab for invoice timeline events."""

    load_requested = Signal()

    def __init__(self) -> None:
        """Build the audit tab controls."""
        super().__init__()
        layout = QVBoxLayout(self)
        load = QPushButton("Load Audit Logs")
        load.clicked.connect(self.load_requested.emit)
        self.timeline = QTextEdit()
        self.timeline.setReadOnly(True)
        layout.addWidget(load)
        layout.addWidget(self.timeline)

    def set_loading(self) -> None:
        """Show a loading state while audit rows are fetched."""
        self.timeline.setPlainText("Loading audit logs...")

    def set_error(self, message: str) -> None:
        """Show an audit loading error."""
        self.timeline.setPlainText(f"Could not load audit logs:\n{message}")

    def set_logs(self, logs: list[dict[str, Any]]) -> None:
        """Render audit log records as a readable timeline."""
        lines = []
        for log in logs:
            lines.append(f"{log.get('timestamp') or ''}\n{log.get('user') or 'system'} - {log.get('action') or ''}\n{log.get('reason') or ''}".strip())
        self.timeline.setPlainText("\n\n".join(lines) if lines else "No audit logs found.")


class DetailPage(QWidget):
    """Invoice detail/review page with metadata, lines, validation, audit, and PDF."""

    back_requested = Signal()
    audit_requested = Signal(int)
    approve_requested = Signal(int)
    corrections_requested = Signal(int, dict)
    reject_requested = Signal(int, str)
    reprocess_requested = Signal(int)
    export_requested = Signal(int, str)

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
        header.addWidget(back)
        header.addWidget(self.title)
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
        pdf_header = QLabel("Invoice PDF")
        pdf_header.setObjectName("sectionTitle")
        pdf_layout.addWidget(pdf_header)
        self.pdf_pages = QVBoxLayout()
        self.pdf_pages.setAlignment(Qt.AlignmentFlag.AlignTop)
        pdf_body = QWidget()
        pdf_body.setLayout(self.pdf_pages)
        self.pdf_scroll = QScrollArea()
        self.pdf_scroll.setWidgetResizable(True)
        self.pdf_scroll.setWidget(pdf_body)
        pdf_layout.addWidget(self.pdf_scroll, stretch=1)
        self.pdf_status = QLabel("")
        self.pdf_status.setObjectName("muted")
        pdf_layout.addWidget(self.pdf_status)

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
        self.title.setText(f"Invoice #{invoice.get('id')} - {invoice.get('status')}")
        self.metadata.load_data(self.original_data)
        self.line_items.load_items(self.original_data.get("line_items") or [])
        self.validation.set_validation(invoice.get("validation"))
        self.audit.set_logs([])
        self.dirty = False
        self.sync_actions()

    def set_pdf(self, pdf_path: Path) -> None:
        """Render the invoice PDF preview into the right-hand scroll panel."""
        self.pdf_status.setText(str(pdf_path))
        clear_layout(self.pdf_pages)
        try:
            for image_path in render_pdf_to_images(pdf_path):
                page = QLabel()
                page.setAlignment(Qt.AlignmentFlag.AlignCenter)
                page.setPixmap(QPixmap(str(image_path)))
                page.setObjectName("pdfPage")
                self.pdf_pages.addWidget(page)
            self.pdf_pages.addStretch()
        except Exception as exc:
            fallback = QLabel(f"Could not render PDF preview.\n{exc}")
            fallback.setWordWrap(True)
            self.pdf_pages.addWidget(fallback)

    def mark_dirty(self) -> None:
        """Mark the invoice form as edited."""
        self.dirty = True
        self.sync_actions()

    def sync_actions(self) -> None:
        """Enable or disable review actions based on invoice state."""
        status = (self.invoice or {}).get("status")
        reviewable = status in {"Pending_Review", "Rejected", "Extracted"}
        self.approve_btn.setEnabled(reviewable)
        self.reject_btn.setEnabled(reviewable)
        self.corrections_btn.setEnabled(reviewable and self.dirty)

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
        if self.invoice_id() is not None:
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


class MainWindow(QMainWindow):
    """Top-level desktop application window and workflow coordinator."""

    def __init__(self) -> None:
        """Initialize the workflow service, pages, sidebar, and health timer."""
        super().__init__()
        self.workflow = DesktopWorkflow()
        self.workflow.initialize()
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[Worker] = set()
        self.setWindowTitle("Invoice AI Desktop")
        self.resize(1280, 820)
        root = QSplitter(Qt.Orientation.Horizontal)
        root.setHandleWidth(1)
        root.addWidget(self.build_sidebar())
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage()
        self.invoices = InvoicesPage()
        self.upload = UploadPage()
        self.detail = DetailPage()
        for page in (self.dashboard, self.invoices, self.upload, self.detail):
            self.stack.addWidget(page)
        root.addWidget(self.stack)
        root.setSizes([230, 1050])
        self.setCentralWidget(root)
        self.connect_signals()
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self.check_health)
        self.health_timer.start(10_000)
        self.check_health()
        self.load_dashboard()

    def build_sidebar(self) -> QWidget:
        """Create the app sidebar with navigation and reviewer identity."""
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 24, 18, 18)
        title = QLabel("Invoice AI")
        title.setObjectName("appTitle")
        status_row = QHBoxLayout()
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_text = QLabel("Checking")
        self.status_text.setObjectName("muted")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_text)
        status_row.addStretch()
        dash = QPushButton("Dashboard")
        inv = QPushButton("Invoices")
        upload = QPushButton("Upload Invoice")
        for button in (dash, inv, upload):
            button.setObjectName("navButton")
            button.setMinimumHeight(38)
        dash.clicked.connect(lambda: (self.stack.setCurrentWidget(self.dashboard), self.load_dashboard()))
        inv.clicked.connect(lambda: (self.stack.setCurrentWidget(self.invoices), self.load_invoices()))
        upload.clicked.connect(lambda: self.stack.setCurrentWidget(self.upload))
        reviewer_label = QLabel("Reviewer")
        reviewer_label.setObjectName("sectionTitle")
        self.reviewer = QLineEdit("reviewer")
        layout.addWidget(title)
        layout.addLayout(status_row)
        layout.addSpacing(18)
        layout.addWidget(dash)
        layout.addWidget(inv)
        layout.addWidget(upload)
        layout.addStretch()
        layout.addWidget(reviewer_label)
        layout.addWidget(self.reviewer)
        return sidebar

    def connect_signals(self) -> None:
        """Connect page signals to workflow actions."""
        self.dashboard.refresh_requested.connect(self.load_dashboard)
        self.invoices.refresh_requested.connect(self.load_invoices)
        self.invoices.invoice_selected.connect(self.open_invoice)
        self.upload.upload_requested.connect(self.upload_invoice)
        self.detail.back_requested.connect(lambda: self.stack.setCurrentWidget(self.invoices))
        self.detail.audit_requested.connect(self.load_audit)
        self.detail.approve_requested.connect(self.approve_invoice)
        self.detail.corrections_requested.connect(self.submit_corrections)
        self.detail.reject_requested.connect(self.reject_invoice)
        self.detail.reprocess_requested.connect(self.reprocess_invoice)
        self.detail.export_requested.connect(self.export_invoice)

    def run_task(
        self,
        fn: Callable[..., Any],
        on_result: Callable[[Any], None],
        *args: Any,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Run a workflow call on the thread pool and keep it alive until done."""
        worker = Worker(fn, *args)
        self.active_workers.add(worker)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error or self.show_error)
        worker.signals.finished.connect(lambda w=worker: self.active_workers.discard(w))
        self.thread_pool.start(worker)

    def check_health(self) -> None:
        """Refresh the sidebar readiness indicator."""
        def ok(_result: Any) -> None:
            self.status_dot.setStyleSheet("border-radius: 5px; background: #10b981;")
            self.status_text.setText("Ready")

        self.run_task(self.workflow.health, ok, on_error=lambda _err: self.set_offline())

    def set_offline(self) -> None:
        """Show a database/service error in the sidebar indicator."""
        self.status_dot.setStyleSheet("border-radius: 5px; background: #ef4444;")
        self.status_text.setText("DB error")

    def load_dashboard(self) -> None:
        """Load dashboard statistics in the background."""
        self.run_task(self.workflow.stats, self.dashboard.set_stats)

    def load_invoices(self) -> None:
        """Load invoice list records in the background."""
        self.run_task(self.workflow.list_invoices, self.invoices.set_invoices)

    def upload_invoice(self, path: str) -> None:
        """Start invoice upload and processing in the background."""
        self.upload.set_busy(True, "Processing invoice... AI model is extracting data.")

        def done(invoice: dict[str, Any]) -> None:
            self.upload.set_busy(False, "Upload complete.")
            self.open_invoice(int(invoice["id"]))

        self.run_task(
            self.workflow.upload_invoice,
            done,
            path,
            on_error=lambda err: (self.upload.set_busy(False, ""), self.show_error(err)),
        )

    def open_invoice(self, invoice_id: int) -> None:
        """Open an invoice detail page and load its PDF preview."""
        self.stack.setCurrentWidget(self.detail)

        def loaded(invoice: dict[str, Any]) -> None:
            self.detail.load_invoice(invoice)
            self.run_task(self.workflow.get_pdf_path, self.detail.set_pdf, invoice_id)

        self.run_task(self.workflow.get_invoice, loaded, invoice_id)

    def load_audit(self, invoice_id: int) -> None:
        """Load audit logs for the current detail page."""
        self.detail.audit.set_loading()
        self.run_task(
            self.workflow.audit_log,
            self.detail.audit.set_logs,
            invoice_id,
            on_error=self.detail.audit.set_error,
        )

    def approve_invoice(self, invoice_id: int) -> None:
        """Approve the current invoice without corrections."""
        payload = {"decision": "approve", "reviewer": self.reviewer.text().strip() or "reviewer"}
        self.run_task(lambda: self.workflow.submit_review(invoice_id, payload), lambda _result: self.open_invoice(invoice_id))

    def submit_corrections(self, invoice_id: int, corrections: dict[str, Any]) -> None:
        """Submit manual corrections and approve the invoice."""
        payload = {"decision": "approve_with_corrections", "reviewer": self.reviewer.text().strip() or "reviewer", "corrections": corrections}
        self.run_task(lambda: self.workflow.submit_review(invoice_id, payload), lambda _result: self.open_invoice(invoice_id))

    def reject_invoice(self, invoice_id: int, reason: str) -> None:
        """Reject an invoice with a reviewer-provided reason."""
        payload = {"decision": "reject", "reviewer": self.reviewer.text().strip() or "reviewer", "rejection_reason": reason}
        self.run_task(lambda: self.workflow.submit_review(invoice_id, payload), lambda _result: self.open_invoice(invoice_id))

    def reprocess_invoice(self, invoice_id: int) -> None:
        """Re-run extraction and validation for an invoice."""
        self.run_task(self.workflow.reprocess_invoice, lambda _result: self.open_invoice(invoice_id), invoice_id)

    def export_invoice(self, invoice_id: int, fmt: str) -> None:
        """Export an approved invoice or push it to ERPNext."""
        if fmt == "erpnext":
            self.run_task(lambda: self.workflow.export_invoice(invoice_id, fmt), lambda _result: QMessageBox.information(self, "ERPNext", "Invoice pushed to ERPNext."))
            return
        ext = "xml" if fmt == "tally" else fmt
        path, _ = QFileDialog.getSaveFileName(self, "Save Export", f"invoice_{invoice_id}.{ext}")
        if not path:
            return

        def save(result: tuple[bytes, str | None]) -> None:
            content, _filename = result
            if not isinstance(content, bytes):
                raise TypeError("Export did not return file content.")
            Path(path).write_bytes(content)
            QMessageBox.information(self, "Export Complete", f"Saved export to:\n{path}")

        self.run_task(lambda: self.workflow.export_invoice(invoice_id, fmt), save)

    def show_error(self, message: str) -> None:
        """Display a workflow error dialog."""
        QMessageBox.critical(self, "Invoice AI Desktop", message)


def cast_field(name: str, value: str) -> Any:
    """Cast metadata form text into values accepted by invoice schemas."""
    text = value.strip()
    if text == "":
        return None
    if name in NUMERIC_FIELDS:
        try:
            return float(text)
        except ValueError:
            return 0.0
    return text


def cast_line_field(name: str, value: str) -> Any:
    """Cast a line-item table cell into the correct schema value."""
    text = value.strip()
    if text == "":
        return 0.0 if name in LINE_FLOAT_FIELDS else None
    if name == "sr_no":
        try:
            return int(text)
        except ValueError:
            return None
    if name in LINE_FLOAT_FIELDS:
        try:
            return float(text)
        except ValueError:
            return 0.0
    return text


def format_confidence(value: Any) -> str:
    """Format confidence as a percentage for display."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    if numeric <= 1:
        numeric *= 100
    return f"{numeric:.0f}%"


def format_money(value: Any) -> str:
    """Format numeric money values for display."""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "--"


def clear_layout(layout) -> None:
    """Delete all child widgets/layouts from a Qt layout."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def render_pdf_to_images(pdf_path: Path, max_pages: int = 6) -> list[Path]:
    """Render the first PDF pages to temporary PNG files for preview."""
    import pypdfium2 as pdfium

    output_dir = Path(tempfile.gettempdir()) / "invoice_ai_desktop_pdf"
    output_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf_path))
    image_paths: list[Path] = []
    try:
        page_count = min(len(document), max_pages)
        for index in range(page_count):
            page = document[index]
            try:
                bitmap = page.render(scale=1.6)
                image = bitmap.to_pil()
                target = output_dir / f"{pdf_path.stem}_page_{index + 1}.png"
                image.save(target)
                image_paths.append(target)
            finally:
                page.close()
    finally:
        document.close()
    if not image_paths:
        raise ValueError("PDF has no renderable pages.")
    return image_paths


def stylesheet() -> str:
    """Return the application stylesheet."""
    return """
    * { font-family: "Plus Jakarta Sans", "Segoe UI", Arial, sans-serif; font-size: 13px; color: #1f2937; }
    QMainWindow { background: #f6f8fb; }
    #sidebar { background: #ffffff; border-right: 1px solid #e5e7eb; }
    #appTitle { font-size: 24px; font-weight: 800; color: #111827; }
    #pageTitle { font-size: 24px; font-weight: 800; color: #111827; }
    #sectionTitle { font-weight: 700; color: #334155; }
    #muted { color: #64748b; }
    #kpi { font-size: 30px; font-weight: 800; color: #0f172a; }
    QPushButton, QToolButton { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px 12px; font-weight: 600; }
    QPushButton:hover, QToolButton:hover { background: #f1f5f9; }
    QPushButton:disabled { color: #94a3b8; background: #f8fafc; }
    #navButton { text-align: left; padding-left: 12px; }
    QLineEdit, QTextEdit, QTableWidget { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 7px; }
    QTableWidget { gridline-color: #e5e7eb; selection-background-color: #dbeafe; }
    QHeaderView::section { background: #f8fafc; border: none; border-bottom: 1px solid #e5e7eb; padding: 8px; font-weight: 700; }
    #card, #subsection, #pdfPanel { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; }
    #card { min-height: 104px; }
    #dropZone { background: #ffffff; border: 2px dashed #94a3b8; border-radius: 8px; min-height: 340px; }
    #dropTitle { font-size: 22px; font-weight: 800; }
    #statusBar { min-height: 24px; max-height: 24px; border-radius: 6px; background: #e5e7eb; }
    #warningBanner { background: #fffbeb; border: 1px solid #f59e0b; border-radius: 6px; padding: 10px; color: #92400e; }
    #errorBanner { background: #fef2f2; border: 1px solid #ef4444; border-radius: 6px; padding: 10px; color: #991b1b; }
    """


def configure_logging() -> None:
    """Configure terminal and file logging for the desktop app."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "desktop_app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )
    logging.getLogger(__name__).info("Logging initialized: %s", log_file)


def main() -> int:
    """Run the Invoice AI desktop application."""
    configure_logging()
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    app = QApplication(sys.argv)
    app.setStyleSheet(stylesheet())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
