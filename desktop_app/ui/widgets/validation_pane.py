from __future__ import annotations

"""Validation issue display widget."""

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..formatters import clear_layout


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
