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
        layout.addWidget(title)
        layout.addWidget(self.drop_zone, stretch=1)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)

    def set_busy(self, busy: bool, message: str = "") -> None:
        """Toggle upload progress state and status text."""
        self.progress.setVisible(busy)
        self.status.setText(message)
        self.drop_zone.setEnabled(not busy)
