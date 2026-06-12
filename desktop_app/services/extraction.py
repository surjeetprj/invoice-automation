from __future__ import annotations

"""PDF classification and text extraction helpers for desktop processing."""

import logging
from pathlib import Path

import pdfplumber
from pypdf.errors import PdfReadError

from ..config import MAX_FILE_SIZE_MB

logger = logging.getLogger(__name__)

TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 3,
    "join_tolerance": 3,
}


class ScannedDocumentException(Exception):
    """Raised when the uploaded PDF appears to be scanned/image-only."""


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


def validate_file(path: Path) -> None:
    """Validate a PDF before extraction."""
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF invoices are supported.")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValueError(f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")
    classification = classify_pdf(path)
    if classification == "SCANNED":
        raise ScannedDocumentException("Scanned document detected. Upload a system-generated PDF invoice.")


def extract_invoice_text(file_path: str | Path) -> str:
    """Extract layout-preserved PDF text plus detected Markdown tables."""
    path = Path(file_path)
    validate_file(path)
    parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        if page_count == 0:
            raise ValueError("The PDF does not contain any pages.")
        for page_number, page in enumerate(pdf.pages, start=1):
            page_text, page_tables = extract_page_content(page, page_number)
            parts.append(page_text)
            if page_tables:
                parts.append(f"\n\n## Extracted Tables - Page {page_number}\n")
                parts.extend(page_tables)
    text = "\n\n".join(part for part in parts if part.strip())
    if not text.strip():
        raise ValueError("No selectable text was found in the PDF.")
    return text


def extract_page_content(page, page_number: int) -> tuple[str, list[str]]:
    """Return layout text plus markdown tables for one pdfplumber page."""
    page_text = page.extract_text(layout=True) or ""
    table_markdown: list[str] = []
    try:
        tables = page.extract_tables(table_settings=TABLE_SETTINGS) or []
    except Exception as exc:
        logger.warning("Table extraction failed on page %s: %s", page_number, exc)
        return page_text, table_markdown

    for table_index, table in enumerate(tables, start=1):
        markdown = table_to_markdown(table, title=f"Table {table_index}")
        if markdown:
            table_markdown.append(markdown)
    return page_text, table_markdown


def table_to_markdown(table: list[list[str | None]], title: str | None = None) -> str:
    """Convert a pdfplumber table into compact Markdown for the AI parser."""
    rows = normalize_table_rows(table)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    body = rows[1:] or [[""] * width]
    lines: list[str] = []
    if title:
        lines.append(f"### {title}")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def normalize_table_rows(table: list[list[str | None]]) -> list[list[str]]:
    """Clean empty rows and normalize cell text from pdfplumber tables."""
    rows: list[list[str]] = []
    for raw_row in table or []:
        row = [clean_cell(cell) for cell in raw_row]
        if any(row):
            rows.append(row)
    return rows


def clean_cell(value: str | None) -> str:
    """Normalize one table cell for safe Markdown output."""
    if value is None:
        return ""
    return " ".join(str(value).replace("|", "/").split())
