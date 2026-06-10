from __future__ import annotations

"""PDF preview rendering and display widgets."""

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QToolBar, QVBoxLayout, QWidget

from ..formatters import clear_layout


def render_pdf_to_images(pdf_path: Path, scale: float = 1.6, max_pages: int = 6) -> list[Path]:
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
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                target = output_dir / f"{pdf_path.stem}_page_{index + 1}_{int(scale * 100)}.png"
                image.save(target)
                image_paths.append(target)
            finally:
                page.close()
    finally:
        document.close()
    if not image_paths:
        raise ValueError("PDF has no renderable pages.")
    return image_paths


class PdfPreview(QWidget):
    """Scroll-based PDF image preview with basic zoom controls."""

    zoom_requested = Signal(float)

    def __init__(self) -> None:
        """Create the PDF preview toolbar and page area."""
        super().__init__()
        self.zoom = 1.6
        self.pdf_path: Path | None = None
        layout = QVBoxLayout(self)
        toolbar = QToolBar()
        zoom_out = QPushButton("Zoom -")
        zoom_in = QPushButton("Zoom +")
        fit = QPushButton("Fit Width")
        zoom_out.clicked.connect(lambda: self.set_zoom(max(0.8, self.zoom - 0.2)))
        zoom_in.clicked.connect(lambda: self.set_zoom(min(3.0, self.zoom + 0.2)))
        fit.clicked.connect(lambda: self.set_zoom(1.6))
        toolbar.addWidget(zoom_out)
        toolbar.addWidget(zoom_in)
        toolbar.addWidget(fit)
        self.page_count_label = QLabel("No PDF")
        toolbar.addWidget(self.page_count_label)
        layout.addWidget(toolbar)
        self.pages = QVBoxLayout()
        self.pages.setAlignment(Qt.AlignmentFlag.AlignTop)
        body = QWidget()
        body.setLayout(self.pages)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(body)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        layout.addWidget(self.scroll, stretch=1)
        layout.addWidget(self.status)

    def set_loading(self, pdf_path: Path) -> None:
        """Show a loading state before rendered pages are available."""
        self.pdf_path = pdf_path
        self.status.setText(str(pdf_path))
        self.page_count_label.setText("Rendering PDF...")
        clear_layout(self.pages)
        self.pages.addWidget(QLabel("Rendering PDF preview..."))

    def set_pages(self, image_paths: list[Path]) -> None:
        """Display rendered PDF page images."""
        clear_layout(self.pages)
        for image_path in image_paths:
            page = QLabel()
            page.setAlignment(Qt.AlignmentFlag.AlignCenter)
            page.setPixmap(QPixmap(str(image_path)))
            page.setObjectName("pdfPage")
            self.pages.addWidget(page)
        self.pages.addStretch()
        self.page_count_label.setText(f"{len(image_paths)} page(s)")

    def set_error(self, message: str) -> None:
        """Show a PDF rendering error."""
        clear_layout(self.pages)
        label = QLabel(f"Could not render PDF preview.\n{message}")
        label.setWordWrap(True)
        self.pages.addWidget(label)
        self.page_count_label.setText("Preview error")

    def set_zoom(self, zoom: float) -> None:
        """Update zoom for the next render request."""
        self.zoom = zoom
        if self.pdf_path:
            self.set_loading(self.pdf_path)
            self.zoom_requested.emit(zoom)
