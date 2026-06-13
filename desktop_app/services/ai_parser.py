from __future__ import annotations

"""Public AI parser facade kept stable for workflow and tests."""

from typing import Any

from ..domain.schemas import InvoiceData, SupplyType
from .ai_client import invoke_invoice_file_parser, invoke_invoice_parser
from .document_source import DocumentKind, InvoiceSource, ParsedInvoiceResult
from .extraction import extract_invoice_text
from .invoice_normalizer import normalize_extracted_data, to_float
from .raw_text_enrichment import enrich_from_raw_text


def parse_invoice(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Parse layout-preserved invoice text into an InvoiceData dictionary."""
    data = invoke_invoice_parser(raw_markdown, vendor_hint)
    enrich_from_raw_text(data, raw_markdown)
    return normalize_extracted_data(data)


def parse_invoice_file(
    file_path,
    mime_type: str,
    vendor_hint: str | None = None,
    document_kind: str | None = None,
) -> dict[str, Any]:
    """Parse an image invoice or scanned PDF directly with Gemini vision."""
    data = invoke_invoice_file_parser(file_path, mime_type, vendor_hint)
    return normalize_extracted_data(data, document_kind=document_kind)


def parse_invoice_source(source: InvoiceSource, vendor_hint: str | None = None) -> ParsedInvoiceResult:
    """Parse a classified invoice source through the correct AI path."""
    if source.document_kind == DocumentKind.DIGITAL_PDF:
        raw_markdown = source.text_context or extract_invoice_text(source.path)
        data = parse_invoice(raw_markdown, vendor_hint)
        return ParsedInvoiceResult(
            data=data,
            source_text=raw_markdown,
            document_kind=source.document_kind.value,
            mime_type=source.mime_type,
        )

    data = parse_invoice_file(source.path, source.mime_type, vendor_hint, document_kind=source.document_kind.value)
    return ParsedInvoiceResult(
        data=data,
        source_text=None,
        document_kind=source.document_kind.value,
        mime_type=source.mime_type,
    )


def empty_invoice(vendor_hint: str | None = None) -> dict[str, Any]:
    """Return an empty but schema-valid invoice payload."""
    return InvoiceData(vendor_name=vendor_hint, supply_type=SupplyType.UNKNOWN).model_dump()
