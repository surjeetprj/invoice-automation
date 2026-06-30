from __future__ import annotations

"""Document upload validation, MIME detection, and routing classification."""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pdfplumber
from pypdf.errors import PdfReadError

from ...config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)

PDF_EXTENSION = ".pdf"
IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MIME_TYPES = {PDF_EXTENSION: "application/pdf", **IMAGE_MIME_TYPES}
MIN_DIGITAL_TEXT_CHARS = 100
SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.8


class DocumentKind(str, Enum):
    """Supported invoice source classes."""

    DIGITAL_PDF = "DIGITAL_PDF"
    SCANNED_PDF = "SCANNED_PDF"
    IMAGE = "IMAGE"


@dataclass(frozen=True)
class InvoiceSource:
    """Classified invoice source passed into the parser facade."""

    path: Path
    document_kind: DocumentKind
    mime_type: str
    text_context: str | None = None


@dataclass(frozen=True)
class ParsedInvoiceResult:
    """Parser output plus source metadata used by workflow persistence."""

    data: dict
    source_text: str | None
    document_kind: str
    mime_type: str


def validate_upload_file(path: Path) -> None:
    """Validate existence, extension, and size for invoice uploads."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Upload path is not a file: {path}")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        supported = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise ValueError(f"Only invoice files with these extensions are supported: {supported}")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")


def mime_type_for_path(path: Path) -> str:
    """Return the supported MIME type for an upload path."""
    suffix = path.suffix.lower()
    try:
        return MIME_TYPES[suffix]
    except KeyError as exc:
        supported = ", ".join(sorted(MIME_TYPES))
        raise ValueError(f"Unsupported invoice file type '{suffix}'. Supported types: {supported}") from exc


def classify_document(file_path: str | Path) -> InvoiceSource:
    """Return source routing metadata for a PDF or image invoice."""
    path = Path(file_path)
    validate_upload_file(path)
    suffix = path.suffix.lower()
    mime_type = mime_type_for_path(path)
    if suffix in IMAGE_MIME_TYPES:
        return InvoiceSource(path=path, document_kind=DocumentKind.IMAGE, mime_type=mime_type)
    return InvoiceSource(path=path, document_kind=classify_pdf(path), mime_type=mime_type)


def classify_pdf(file_path: str | Path) -> DocumentKind:
    """Classify a PDF as digital or scanned/image-only."""
    path = Path(file_path)
    try:
        if first_page_is_image_heavy(path):
            return DocumentKind.SCANNED_PDF
        if selectable_text_length(path) >= MIN_DIGITAL_TEXT_CHARS:
            return DocumentKind.DIGITAL_PDF
        return DocumentKind.SCANNED_PDF
    except Exception as exc:
        logger.warning("Could not classify PDF %s: %s", path.name, exc)
        return DocumentKind.SCANNED_PDF


def first_page_is_image_heavy(path: Path) -> bool:
    """Return True when the first PDF page is mostly one embedded image."""
    with pdfplumber.open(path) as pdf:
        if not pdf.pages:
            raise ValueError("The PDF does not contain any pages.")
        first_page = pdf.pages[0]
        page_area = first_page.width * first_page.height
        if not page_area:
            return False
        for image in first_page.images:
            image_area = (image.get("x1", 0) - image.get("x0", 0)) * (image.get("y1", 0) - image.get("y0", 0))
            if image_area / page_area > SCANNED_IMAGE_COVERAGE_THRESHOLD:
                return True
    return False


def selectable_text_length(path: Path, max_pages: int = 2) -> int:
    """Return selectable text length from the first few PDF pages."""
    text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:max_pages]:
            text += (page.extract_text() or "").strip()
    return len(text)
