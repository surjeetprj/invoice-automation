from __future__ import annotations

"""Drag-and-drop upload widget."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QPushButton, QVBoxLayout


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
