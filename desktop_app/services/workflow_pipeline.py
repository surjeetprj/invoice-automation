from __future__ import annotations

"""Upload and processing pipeline utility helpers."""

import hashlib
from pathlib import Path

from .documents.document_source import DocumentKind


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hash for a file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document_kind_label(kind: DocumentKind) -> str:
    """Return a user-friendly document classification label."""
    labels = {
        DocumentKind.DIGITAL_PDF: "Digital PDF",
        DocumentKind.SCANNED_PDF: "Scanned PDF",
        DocumentKind.IMAGE: "Image invoice",
    }
    return labels.get(kind, kind.value.replace("_", " ").title())
