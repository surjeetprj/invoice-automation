from __future__ import annotations

"""Editable invoice line-item table and GST component helpers."""

import copy
from typing import Any

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..constants import LINE_COLUMNS, LINE_FLOAT_FIELDS, TAX_AMOUNT_FIELDS, TAX_COMPONENTS, TAX_RATE_FIELDS

LINE_INT_FIELDS = {"sr_no"}
INVALID_CELL_COLOR = QColor("#fee2e2")
VALID_CELL_COLOR = QColor("#ffffff")
ORIGINAL_LINE_ITEM_ROLE = Qt.ItemDataRole.UserRole


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
        self.table.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.table.setMinimumHeight(170)
        self.table.itemChanged.connect(self.item_changed)
        layout.addLayout(actions)
        layout.addWidget(self.table)

    def load_items(self, items: list[dict[str, Any]]) -> None:
        """Populate the table from extracted line item data."""
        self.loading = True
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            original_item = copy.deepcopy(item)
            flat_item = flatten_line_item_taxes(item)
            for col, (name, _label) in enumerate(LINE_COLUMNS):
                value = flat_item.get(name)
                cell = QTableWidgetItem("" if value is None else str(value))
                if col == 0:
                    cell.setData(ORIGINAL_LINE_ITEM_ROLE, original_item)
                self.table.setItem(row, col, cell)
        self.loading = False
        self.adjust_visible_height()

    def add_line(self) -> None:
        """Append a blank line item row."""
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, (name, _label) in enumerate(LINE_COLUMNS):
            self.table.setItem(row, col, QTableWidgetItem(str(row + 1) if name == "sr_no" else ""))
        self.adjust_visible_height()
        self.changed.emit()

    def remove_line(self) -> None:
        """Remove the currently selected line item row."""
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.adjust_visible_height()
            self.changed.emit()

    def adjust_visible_height(self) -> None:
        """Keep embedded line rows visible inside the metadata scroll area."""
        visible_rows = max(min(self.table.rowCount(), 6), 2)
        header_height = self.table.horizontalHeader().height()
        row_height = self.table.verticalHeader().defaultSectionSize()
        frame_padding = self.table.frameWidth() * 2 + 18
        self.table.setMinimumHeight(max(170, header_height + (visible_rows * row_height) + frame_padding))

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
        return [
            build_line_item_taxes(self.row_values(row), original_item=self.original_item_for_row(row))
            for row in range(self.table.rowCount())
        ]

    def original_item_for_row(self, row: int) -> dict[str, Any] | None:
        """Return the original nested line item associated with a table row."""
        item = self.table.item(row, 0)
        original = item.data(ORIGINAL_LINE_ITEM_ROLE) if item is not None else None
        return copy.deepcopy(original) if isinstance(original, dict) else None


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


def build_line_item_taxes(values: dict[str, Any], original_item: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild nested tax rows from editable columns while preserving hidden tax details."""
    item = {
        key: value
        for key, value in values.items()
        if key not in TAX_RATE_FIELDS and key not in TAX_AMOUNT_FIELDS
    }
    taxable_value = values.get("taxable_value") or 0.0
    component_taxes = {
        component.upper(): {
            "tax_type": component.upper(),
            "tax_rate": values.get(f"{component}_rate") or 0.0,
            "taxable_amount": taxable_value,
            "tax_amount": values.get(f"{component}_amount") or 0.0,
        }
        for component in TAX_COMPONENTS
    }
    taxes = []
    added_components: set[str] = set()
    for tax in (original_item or {}).get("taxes") or []:
        if not isinstance(tax, dict):
            continue
        tax_type = str(tax.get("tax_type") or "").strip().upper()
        if tax_type in component_taxes:
            component_tax = component_taxes[tax_type]
            if tax_type not in added_components and (component_tax["tax_rate"] > 0 or component_tax["tax_amount"] > 0):
                taxes.append(component_tax)
                added_components.add(tax_type)
            continue
        taxes.append(dict(tax))
    for tax_type, component_tax in component_taxes.items():
        if tax_type not in added_components and (component_tax["tax_rate"] > 0 or component_tax["tax_amount"] > 0):
            taxes.append(component_tax)
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
