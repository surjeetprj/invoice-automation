from __future__ import annotations

"""Public AI parser facade kept stable for workflow and tests."""

from typing import Any

from ..domain.schemas import InvoiceData, SupplyType
from .ai_client import invoke_invoice_parser
from .invoice_normalizer import normalize_extracted_data, to_float
from .raw_text_enrichment import enrich_from_raw_text


def parse_invoice(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Parse layout-preserved invoice text into an InvoiceData dictionary."""
    data = invoke_invoice_parser(raw_markdown, vendor_hint)
    enrich_from_raw_text(data, raw_markdown)
    return normalize_extracted_data(data)


def empty_invoice(vendor_hint: str | None = None) -> dict[str, Any]:
    """Return an empty but schema-valid invoice payload."""
    return InvoiceData(vendor_name=vendor_hint, supply_type=SupplyType.UNKNOWN).model_dump()
