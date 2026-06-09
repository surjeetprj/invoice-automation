from __future__ import annotations

"""AI invoice parsing and normalization for the desktop workflow."""

import logging
from typing import Any

from config import GOOGLE_API_KEY
from schemas import InvoiceData, SupplyType

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert Indian GST invoice processing agent.
Extract all visible invoice fields into the InvoiceData schema. Preserve nulls
for missing fields. Detect GST supply type from vendor/customer GSTIN state
codes. Extract every line item, taxes, totals, bank details, e-invoice fields,
transport details, reverse charge, and confidence score. Return dates as
DD-MM-YYYY and do not hallucinate values."""


def parse_invoice(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Parse layout-preserved invoice text into an InvoiceData dictionary."""
    if not GOOGLE_API_KEY:
        logger.warning("GOOGLE_API_KEY is not configured; returning empty extraction payload.")
        return empty_invoice(vendor_hint)

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-lite",
            google_api_key=GOOGLE_API_KEY,
            temperature=0.0,
        )
        structured_llm = llm.with_structured_output(InvoiceData)
        result = structured_llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"Invoice text:\n\n{raw_markdown}\n\nVendor hint: {vendor_hint or 'Unknown'}"),
        ])
        data = result.model_dump()
        return normalize_extracted_data(data)
    except Exception as exc:
        logger.exception("AI parsing failed: %s", exc)
        fallback = empty_invoice(vendor_hint)
        fallback["raw_parser_error"] = str(exc)
        return fallback


def normalize_extracted_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize GSTINs, supply type, and numeric fields after AI parsing."""
    for field in ("vendor_gstin", "customer_gstin", "shipping_gstin"):
        if isinstance(data.get(field), str):
            data[field] = data[field].strip().upper().replace(" ", "")

    vendor_gstin = data.get("vendor_gstin") or ""
    customer_gstin = data.get("customer_gstin") or ""
    if len(vendor_gstin) == 15:
        data["vendor_state_code"] = vendor_gstin[:2]
        data["vendor_pan"] = vendor_gstin[2:12]
    if len(customer_gstin) == 15:
        data["customer_state_code"] = customer_gstin[:2]
        data["customer_pan"] = customer_gstin[2:12]
    if data.get("vendor_state_code") and data.get("customer_state_code"):
        data["supply_type"] = (
            SupplyType.INTRA_STATE.value
            if data["vendor_state_code"] == data["customer_state_code"]
            else SupplyType.INTER_STATE.value
        )

    for field in (
        "total_taxable_amount", "total_cgst", "total_sgst", "total_igst",
        "total_cess", "total_tax_amount", "round_off", "total_amount",
    ):
        data[field] = to_float(data.get(field))

    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        for field in ("quantity", "rate", "discount", "taxable_value", "cess_amount", "total"):
            item[field] = to_float(item.get(field))
    return data


def to_float(value: Any) -> float:
    """Convert common invoice amount values to float."""
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("₹", "").replace(",", "").strip())
    except ValueError:
        return 0.0


def empty_invoice(vendor_hint: str | None = None) -> dict[str, Any]:
    """Return an empty but schema-valid invoice payload."""
    return InvoiceData(vendor_name=vendor_hint, supply_type=SupplyType.UNKNOWN).model_dump()
