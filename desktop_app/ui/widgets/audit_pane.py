from __future__ import annotations

"""Audit log display widget."""

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QTextEdit, QVBoxLayout, QWidget


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
