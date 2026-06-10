from __future__ import annotations

"""Invoice list page."""

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .constants import STATUS_COLORS
from .formatters import format_confidence, format_money


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
        self.empty_label = QLabel("")
        self.empty_label.setObjectName("muted")
        layout.addWidget(self.empty_label)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["ID", "Vendor", "Invoice No", "Date", "Total Amount", "Confidence", "Status"])
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.open_row)
        layout.addWidget(self.table)

    def set_loading(self) -> None:
        """Show a loading state while invoices are fetched."""
        self.empty_label.setText("Loading invoices...")

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
        self.empty_label.setText("No invoices found." if not rows else "")
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
                if col == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col == 6:
                    item.setForeground(QColor(STATUS_COLORS.get(str(value), "#334155")))
                self.table.setItem(row, col, item)

    def open_row(self, item: QTableWidgetItem) -> None:
        """Emit the selected invoice ID when a row is opened."""
        invoice_id = self.table.item(item.row(), 0).data(Qt.ItemDataRole.UserRole)
        if invoice_id is not None:
            self.invoice_selected.emit(int(invoice_id))
