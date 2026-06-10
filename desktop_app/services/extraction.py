from __future__ import annotations

"""PDF classification and text extraction helpers for desktop processing."""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pypdf.errors import PdfReadError

from ..config import MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)


class ScannedDocumentException(Exception):
    """Raised when the uploaded PDF appears to be scanned/image-only."""


@dataclass
class ExtractionMetadata:
    """Diagnostics captured during PDF text extraction."""

    file_name: str = ""
    file_size_bytes: int = 0
    page_count: int = 0
    table_count: int = 0
    character_count: int = 0
    extraction_time_ms: int = 0
    has_tables: bool = False
    quality_notes: list[str] = field(default_factory=list)


def classify_pdf(file_path: Path) -> str:
    """Classify a PDF as DIGITAL or SCANNED using image coverage and text."""
    try:
        with pdfplumber.open(file_path) as pdf:
            if pdf.pages:
                first_page = pdf.pages[0]
                page_area = first_page.width * first_page.height
                for image in first_page.images:
                    image_area = (image.get("x1", 0) - image.get("x0", 0)) * (image.get("y1", 0) - image.get("y0", 0))
                    if page_area and image_area / page_area > 0.8:
                        return "SCANNED"

        from pypdf import PdfReader

        reader = PdfReader(file_path)
        text = ""
        for index in range(min(2, len(reader.pages))):
            text += (reader.pages[index].extract_text() or "").strip()
        return "DIGITAL" if len(text) > 100 else "SCANNED"
    except PdfReadError as exc:
        raise ValueError("The PDF appears to be encrypted, corrupted, or unreadable.") from exc
    except Exception as exc:
        logger.warning("Could not classify PDF %s: %s", file_path.name, exc)
        return "SCANNED"


def validate_file(path: Path) -> list[str]:
    """Validate a PDF before extraction and return quality notes."""
    notes: list[str] = []
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF invoices are supported.")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")
    if size_mb < 0.005:
        notes.append("Very small PDF; extraction quality may be poor.")
    classification = classify_pdf(path)
    if classification == "SCANNED":
        raise ScannedDocumentException("Scanned document detected. Upload a system-generated PDF invoice.")
    return notes


def extract_with_metadata(file_path: str | Path) -> tuple[str, ExtractionMetadata]:
    """Extract layout-preserved PDF text and extraction diagnostics."""
    path = Path(file_path)
    notes = validate_file(path)
    start = time.perf_counter()
    text = ""
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        if page_count == 0:
            raise ValueError("The PDF does not contain any pages.")
        for page in pdf.pages:
            text += page.extract_text(layout=True) or ""
    if not text.strip():
        raise ValueError("No selectable text was found in the PDF.")
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return text, ExtractionMetadata(
        file_name=path.name,
        file_size_bytes=path.stat().st_size,
        page_count=page_count,
        table_count=text.count("\n"),
        character_count=len(text),
        extraction_time_ms=elapsed_ms,
        has_tables="|" in text or "   " in text,
        quality_notes=notes,
    )
