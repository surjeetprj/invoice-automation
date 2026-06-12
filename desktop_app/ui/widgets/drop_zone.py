from __future__ import annotations

"""Drag-and-drop upload widget."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QPushButton, QVBoxLayout

from ...config import ALLOWED_EXTENSIONS

FILE_DIALOG_FILTER = "Invoice files (*.pdf *.png *.jpg *.jpeg *.webp)"


class DropZone(QFrame):
    """Drag-and-drop invoice upload area."""

    file_dropped = Signal(str)

    def __init__(self) -> None:
        """Create the upload dropzone and file picker button."""
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropZone")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Drop invoice PDF or image here")
        title.setObjectName("dropTitle")
        hint = QLabel("or choose a PDF, PNG, JPG, JPEG, or WEBP under 10 MB")
        hint.setObjectName("muted")
        button = QPushButton("Choose Invoice")
        button.clicked.connect(self.choose_file)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        """Accept drag events only for supported local invoice files."""
        if event.mimeData().hasUrls() and Path(event.mimeData().urls()[0].toLocalFile()).suffix.lower() in ALLOWED_EXTENSIONS:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        """Emit the dropped local file path."""
        self.file_dropped.emit(event.mimeData().urls()[0].toLocalFile())

    def choose_file(self) -> None:
        """Open a file dialog and emit the selected invoice path."""
        path, _ = QFileDialog.getOpenFileName(self, "Select invoice file", "", FILE_DIALOG_FILTER)
        if path:
            self.file_dropped.emit(path)
