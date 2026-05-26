"""
Document Extraction Engine — pdfplumber-based layout-preserving pipeline.

Handles PDFs natively. Outputs structured text that preserves spatial layout
and table structures without the overhead of heavy deep learning layout models.
"""
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
import pdfplumber

from config import MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Classification & Exceptions
# ──────────────────────────────────────────────
class ScannedDocumentException(Exception):
    """Raised when an uploaded document is identified as a scanned PDF or raw image."""
    pass


def classify_pdf(file_path: Path) -> str:
    """
    Verify if a PDF is a system-generated (digital) PDF or a scanned document.
    Returns 'DIGITAL' or 'SCANNED'.
    """
    suffix = file_path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"}:
        return "SCANNED"

    try:
        # Step 1: Check for full-page scanned images using pdfplumber
        with pdfplumber.open(file_path) as pdf:
            if len(pdf.pages) > 0:
                first_page = pdf.pages[0]
                page_area = first_page.width * first_page.height
                if page_area > 0:
                    for img in first_page.images:
                        x0 = img.get("x0", 0)
                        y0 = img.get("y0", 0)
                        x1 = img.get("x1", 0)
                        y1 = img.get("y1", 0)
                        w = x1 - x0
                        h = y1 - y0
                        img_area = w * h
                        ratio = img_area / page_area
                        # If a single image covers more than 80% of the page area, it's a scanned PDF
                        if ratio > 0.8:
                            logger.info(f"PDF {file_path.name} classified as SCANNED because image covers {ratio:.2%} of first page.")
                            return "SCANNED"

        # Step 2: Fallback to character count check (using pypdf)
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        total_text = ""
        # Check first 2 pages for text
        for i in range(min(2, len(reader.pages))):
            page_text = reader.pages[i].extract_text() or ""
            total_text += page_text.strip()

        if len(total_text) > 100:
            return "DIGITAL"
        return "SCANNED"
    except Exception as e:
        logger.error(f"Error classifying PDF {file_path.name}: {e}")
        return "SCANNED"



# ──────────────────────────────────────────────
# Extraction Metadata
# ──────────────────────────────────────────────
@dataclass
class ExtractionMetadata:
    """Diagnostics from the pdfplumber extraction pass."""
    file_name: str = ""
    file_size_bytes: int = 0
    page_count: int = 0
    table_count: int = 0
    character_count: int = 0
    extraction_time_ms: int = 0
    has_tables: bool = False
    quality_notes: list[str] = field(default_factory=list)


def initialize_ocr():
    """Lightweight initialization for layout-preserving pdfplumber parser."""
    logger.info("pdfplumber Layout Engine initialized (High-Speed Rule-Based Mode).")


def _validate_file(path: Path) -> list[str]:
    """
    Pre-extraction file validation.
    Returns a list of quality notes/warnings. Raises on hard failures.
    """
    notes: list[str] = []

    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    # File size check
    file_size_mb = path.stat().st_size / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(
            f"File '{path.name}' is {file_size_mb:.1f} MB — "
            f"exceeds the {MAX_FILE_SIZE_MB} MB limit."
        )
    if file_size_mb < 0.005:  # < 5 KB — likely empty or corrupt
        notes.append(f"Very small file ({file_size_mb * 1024:.1f} KB) — quality may be poor")

    # Extension check
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png"}:
        raise ValueError(f"Unsupported file type: {suffix}")

    # Scanned Document check
    classification = classify_pdf(path)
    if classification == "SCANNED":
        raise ScannedDocumentException(
            "Scanned document/image detected. Please upload a system-generated PDF invoice."
        )

    return notes


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def extract_text(file_path: str) -> str:
    """
    Process an invoice PDF using pdfplumber and return
    the document content preserving spatial layout.

    Args:
        file_path: Absolute path to the uploaded PDF.

    Returns:
        A layout-preserved text string representing the full document content.
    """
    path = Path(file_path)
    _validate_file(path)

    logger.info("pdfplumber: Extracting layout-preserved text from '%s'…", path.name)
    
    with pdfplumber.open(path) as pdf:
        text = ""
        for page in pdf.pages:
            text += page.extract_text(layout=True) or ""

    logger.info(
        "pdfplumber: Extraction complete — %d characters from '%s'",
        len(text),
        path.name,
    )
    return text


def extract_with_metadata(file_path: str) -> tuple[str, ExtractionMetadata]:
    """
    Process an invoice PDF using pdfplumber and return both the layout-preserved
    content and extraction diagnostics.

    Args:
        file_path: Absolute path to the uploaded PDF.

    Returns:
        Tuple of (layout_preserved_text, ExtractionMetadata).
    """
    path = Path(file_path)
    quality_notes = _validate_file(path)

    logger.info("pdfplumber: Extracting from '%s' (with metadata)…", path.name)
    start_time = time.perf_counter()

    with pdfplumber.open(path) as pdf:
        text = ""
        page_count = len(pdf.pages)
        for page in pdf.pages:
            text += page.extract_text(layout=True) or ""

    elapsed_ms = int((time.perf_counter() - start_time) * 1000)

    # Estimate tables
    has_tables = "|" in text or "   " in text
    table_count = text.count("\n")
    
    metadata = ExtractionMetadata(
        file_name=path.name,
        file_size_bytes=path.stat().st_size,
        page_count=page_count,
        table_count=table_count,
        character_count=len(text),
        extraction_time_ms=elapsed_ms,
        has_tables=has_tables,
        quality_notes=quality_notes,
    )

    logger.info(
        "pdfplumber: Extraction complete — %d chars, %d pages, %d ms from '%s'",
        len(text), page_count, elapsed_ms, path.name,
    )

    return text, metadata
