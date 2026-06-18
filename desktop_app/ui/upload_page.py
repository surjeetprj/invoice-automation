from __future__ import annotations

"""Invoice upload page."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from .widgets.drop_zone import DropZone


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
        self.status.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(self.drop_zone, stretch=1)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

    def set_busy(self, busy: bool, message: str = "") -> None:
        """Toggle upload progress state and status text."""
        self.progress.setVisible(busy)
        self.drop_zone.setEnabled(not busy)
        if busy:
            if message:
                self.set_activity(message)
        else:
            self.set_status_message(message)

    def set_status_message(self, message: str, *, level: str = "info") -> None:
        """Show one upload status message."""
        self.status.setText(message)
        self.status.setObjectName("activityError" if level == "error" else "muted")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def set_activity(self, payload: object) -> None:
        """Show the latest transient processing message."""
        if isinstance(payload, dict):
            message = str(payload.get("message") or "").strip()
            level = str(payload.get("level") or "info").strip().lower()
        else:
            message = str(payload or "").strip()
            level = "info"
        if not message:
            return
        self.set_status_message(message, level=level)
