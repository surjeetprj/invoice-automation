from __future__ import annotations

"""Editable Tally master mapping rows shown on invoice review."""

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QComboBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class TallyMappingsTable(QWidget):
    """Small editable table for invoice-specific Tally mapping values."""

    changed = Signal()
    HEADERS = ["Mapping", "Invoice Value", "Tally Value"]

    def __init__(self) -> None:
        super().__init__()
        self.original_rows: list[dict[str, Any]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)

        self.table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)

        # Configure columns to resize to contents and never stretch across the page
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.table.setMinimumHeight(120)
        layout.addWidget(self.table)

    def load_mappings(self, rows: list[dict[str, Any]]) -> None:
        """Populate mapping rows from the workflow payload."""
        self.original_rows = [self.persistable_row(row) for row in rows or []]
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row in rows or []:
            row_index = self.table.rowCount()
            self.table.insertRow(row_index)
            self.table.setItem(row_index, 0, self.readonly_item(self.display_type(row.get("mapping_type", ""))))
            self.table.setItem(row_index, 1, self.readonly_item(str(row.get("source_value") or "")))
            combo = QComboBox()
            combo.setEditable(True)
            values = []
            for value in [row.get("tally_value"), *(row.get("candidates") or [])]:
                cleaned = str(value or "").strip()
                if cleaned and cleaned not in values:
                    values.append(cleaned)
            combo.addItems(values)
            combo.setCurrentText(str(row.get("tally_value") or ""))
            combo.currentTextChanged.connect(lambda _text: self.changed.emit())
            combo.setProperty("mapping_type", str(row.get("mapping_type") or ""))
            combo.setProperty("source_value", str(row.get("source_value") or ""))
            combo.setProperty("company_name", str(row.get("company_name") or ""))
            self.table.setCellWidget(row_index, 2, combo)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.adjust_visible_height()

    def readonly_item(self, text: str) -> QTableWidgetItem:
        """Return a non-editable table item."""
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def display_type(self, value: str) -> str:
        """Return a reviewer-friendly mapping type label."""
        return str(value or "").replace("_", " ").title()

    def values(self) -> list[dict[str, Any]]:
        """Return all mapping rows in persistence shape."""
        rows: list[dict[str, Any]] = []
        for row_index in range(self.table.rowCount()):
            combo = self.table.cellWidget(row_index, 2)
            if not isinstance(combo, QComboBox):
                continue
            row = {
                "mapping_type": str(combo.property("mapping_type") or ""),
                "source_value": str(combo.property("source_value") or ""),
                "company_name": str(combo.property("company_name") or ""),
                "tally_value": combo.currentText().strip(),
                "is_active": "Y",
            }
            if row["mapping_type"] and row["source_value"]:
                rows.append(row)
        return rows

    def changed_values(self) -> list[dict[str, Any]]:
        """Return only rows changed by the reviewer."""
        current = self.values()
        return [row for row in current if row not in self.original_rows]

    def persistable_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Strip suggestion-only fields from a mapping row."""
        return {
            "mapping_type": str(row.get("mapping_type") or ""),
            "source_value": str(row.get("source_value") or ""),
            "company_name": str(row.get("company_name") or ""),
            "tally_value": str(row.get("tally_value") or "").strip(),
            "is_active": str(row.get("is_active") or "Y"),
        }

    def adjust_visible_height(self) -> None:
        """Keep embedded mapping rows visible inside the metadata scroll area."""
        visible_rows = max(min(self.table.rowCount(), 6), 2)
        header_height = self.table.horizontalHeader().height() or 25
        row_height = self.table.verticalHeader().defaultSectionSize() or 30
        frame_padding = self.table.frameWidth() * 2 + 18
        self.table.setMinimumHeight(max(120, header_height + (visible_rows * row_height) + frame_padding))
