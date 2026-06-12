from __future__ import annotations

"""AI invoice parsing and normalization for the desktop workflow."""

import logging
import re
from typing import Any

from ..config import CURRENCY_DECIMAL_PLACES, GOOGLE_API_KEY, MATH_TOLERANCE, STATE_CODES
from ..domain.parsing import parse_date, parse_decimal
from ..domain.schemas import InvoiceData, SupplyType

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert Indian GST invoice processing agent.
The input contains layout-preserved PDF text followed by Markdown tables that
were extracted from the same invoice. Use both sources.

Extract all visible invoice fields into the InvoiceData schema. Preserve nulls
for missing fields and do not hallucinate values. Return dates as DD-MM-YYYY.

Important GST rules:
- Detect supply type from vendor/customer GSTIN state codes.
- For INTER_STATE invoices, tax should normally be IGST.
- For INTRA_STATE invoices, tax should normally be CGST and SGST.
- Use the taxable amount after line or invoice-level discount.
- If a table has Qty, Rate, Discount, GST, Amount, preserve those values in the
  matching line item and tax rows.
- Extract Bill To and Ship To separately when both sections are visible.
- Prefer the customer company/legal name over a contact person name.
- Extract totals, round off, bank details, e-invoice fields, transport details,
  reverse charge, and confidence score."""

GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z][A-Z][A-Z0-9]\b", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[- ][A-Za-z]{3,9}[- ]\d{2,4})\b"
)
LINE_TABLE_HEADERS = (
    "description", "item", "hsn", "sac", "qty", "quantity", "rate", "amount",
    "taxable", "igst", "cgst", "sgst", "total",
)
GENERIC_TAX_TYPES = {"", "GST", "TAX", "UTGST", "CGST/SGST", "CGST+SGST", "OUTPUT GST"}


def parse_invoice(raw_markdown: str, vendor_hint: str | None = None) -> dict[str, Any]:
    """Parse layout-preserved invoice text into an InvoiceData dictionary."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY is not configured. AI parsing cannot run.")

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
        enrich_from_raw_text(data, raw_markdown)
        return normalize_extracted_data(data)
    except Exception as exc:
        logger.exception("AI parsing failed: %s", exc)
        raise RuntimeError(f"AI parsing failed: {exc}") from exc


def normalize_extracted_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize AI output into internally consistent invoice data."""
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
    else:
        data["supply_type"] = supply_type_value(data.get("supply_type"))
    if not data.get("place_of_supply") and data.get("customer_state_code") in STATE_CODES:
        data["place_of_supply"] = STATE_CODES[data["customer_state_code"]]

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
        for tax in item.get("taxes") or []:
            if not isinstance(tax, dict):
                continue
            tax["tax_type"] = str(tax.get("tax_type") or "").strip().upper()
            for field in ("tax_rate", "taxable_amount", "tax_amount"):
                tax[field] = to_float(tax.get(field))

    normalize_discounted_line_values(data)
    normalize_tax_components(data)
    normalize_tax_totals(data)
    return data


def normalize_tax_totals(data: dict[str, Any]) -> None:
    """Fill aggregate tax total when component tax totals are present."""
    component_tax_total = data["total_cgst"] + data["total_sgst"] + data["total_igst"] + data["total_cess"]
    if data["total_tax_amount"] == 0.0 and component_tax_total > 0.0:
        data["total_tax_amount"] = round(component_tax_total, CURRENCY_DECIMAL_PLACES)
    if data["total_tax_amount"] == 0.0:
        line_tax_total = sum_line_tax(data)
        if line_tax_total > 0.0:
            data["total_tax_amount"] = round(line_tax_total, CURRENCY_DECIMAL_PLACES)


def to_float(value: Any) -> float:
    """Convert common invoice amount values to float with shared parsing rules."""
    try:
        parsed = parse_decimal(value)
    except (TypeError, ValueError):
        return 0.0
    return float(parsed or 0.0)


def enrich_from_raw_text(data: dict[str, Any], raw_text: str) -> None:
    """Fill safe deterministic fields that are visibly present in raw PDF text."""
    enrich_shipping_from_text(data, raw_text)
    enrich_due_date_from_text(data, raw_text)


def enrich_shipping_from_text(data: dict[str, Any], raw_text: str) -> None:
    """Extract a visible Ship To block when structured AI output missed it."""
    if data.get("shipping_address") or not raw_text:
        return
    lines = extract_ship_to_lines(raw_text)
    if not lines:
        return

    gstin = None
    address_parts: list[str] = []
    shipping_name = None
    for line in lines:
        match = GSTIN_PATTERN.search(line)
        if match:
            gstin = match.group(0).upper()
            line = line[:match.start()].strip()
        if not line:
            continue
        if shipping_name is None and looks_like_company_name(line):
            shipping_name = line
        else:
            address_parts.append(line)

    if shipping_name and not data.get("shipping_name"):
        data["shipping_name"] = shipping_name
    if address_parts and not data.get("shipping_address"):
        data["shipping_address"] = clean_address_segment(" ".join(address_parts))
    if gstin and not data.get("shipping_gstin"):
        data["shipping_gstin"] = gstin


def extract_ship_to_lines(raw_text: str) -> list[str]:
    """Return candidate lines from the Ship To column or block."""
    source_lines = [line.rstrip() for line in raw_text.splitlines()]
    for index, line in enumerate(source_lines):
        lower = line.lower()
        if "ship to" not in lower and "shipped to" not in lower:
            continue
        ship_pos = lower.find("ship to")
        if ship_pos < 0:
            ship_pos = lower.find("shipped to")
        candidates: list[str] = []
        for next_line in source_lines[index + 1:index + 10]:
            if is_line_item_header(next_line):
                break
            fragment = next_line[ship_pos:].strip() if ship_pos < len(next_line) else next_line.strip()
            if not fragment and len(next_line) > 20:
                fragment = next_line[len(next_line) // 2:].strip()
            fragment = fragment.strip(" :-")
            if fragment:
                candidates.append(fragment)
        cleaned = [line for line in candidates if not is_section_noise(line)]
        if cleaned:
            return cleaned
    return []


def enrich_due_date_from_text(data: dict[str, Any], raw_text: str) -> None:
    """Extract due date from common labels when Gemini leaves it empty."""
    if data.get("due_date") or not raw_text:
        return
    patterns = (
        r"(?i)\b(?:due date|payment due date|valid upto|valid up to)\b\s*[:\-]?\s*([^\n\r]{1,40})",
        r"(?i)\b(?:terms)\b\s*[:\-]?\s*due on receipt",
    )
    for pattern in patterns:
        match = re.search(pattern, raw_text)
        if not match:
            continue
        if match.lastindex:
            date_match = DATE_PATTERN.search(match.group(1))
            if date_match:
                parsed = parse_date(date_match.group(1))
                if parsed:
                    data["due_date"] = parsed.strftime("%d-%m-%Y")
                    return


def normalize_discounted_line_values(data: dict[str, Any]) -> None:
    """Use visible discounts to align line taxable values with invoice totals."""
    items = [item for item in data.get("line_items") or [] if isinstance(item, dict)]
    if not items:
        return
    for item in items:
        quantity = item.get("quantity", 0.0)
        rate = item.get("rate", 0.0)
        discount = item.get("discount", 0.0)
        taxable_value = item.get("taxable_value", 0.0)
        gross_value = quantity * rate
        if item.get("total", 0.0) == 0.0 and item.get("taxable_value", 0.0) > 0.0:
            item["total"] = item["taxable_value"]
        if quantity > 0 and rate > 0 and discount == 0.0 and taxable_value > 0.0:
            inferred_discount = round(gross_value - taxable_value, CURRENCY_DECIMAL_PLACES)
            if inferred_discount > MATH_TOLERANCE:
                item["discount"] = inferred_discount
                discount = inferred_discount
        if quantity > 0 and rate > 0 and discount > 0:
            expected = round((quantity * rate) - discount, CURRENCY_DECIMAL_PLACES)
            if expected >= 0 and abs(item.get("taxable_value", 0.0) - expected) > 0.01:
                item["taxable_value"] = expected
                if item.get("total", 0.0) in {0.0, rate * quantity}:
                    item["total"] = expected
    invoice_taxable = data.get("total_taxable_amount", 0.0)
    if len(items) == 1 and invoice_taxable > 0 and abs(items[0].get("taxable_value", 0.0) - invoice_taxable) > 0.01:
        quantity = items[0].get("quantity", 0.0)
        rate = items[0].get("rate", 0.0)
        discount = items[0].get("discount", 0.0)
        expected = round((quantity * rate) - discount, CURRENCY_DECIMAL_PLACES) if quantity > 0 and rate > 0 else 0.0
        if abs(expected - invoice_taxable) <= 0.01:
            items[0]["taxable_value"] = invoice_taxable


def normalize_tax_components(data: dict[str, Any]) -> None:
    """Normalize generic tax rows into GST components based on supply type."""
    supply_type = supply_type_value(data.get("supply_type"))
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        taxes = [tax for tax in item.get("taxes") or [] if isinstance(tax, dict)]
        normalized: list[dict[str, Any]] = []
        for tax in taxes:
            tax_type = str(tax.get("tax_type") or "").strip().upper()
            if supply_type == SupplyType.INTER_STATE.value and tax_type in GENERIC_TAX_TYPES:
                tax["tax_type"] = "IGST"
                normalized.append(tax)
            elif supply_type == SupplyType.INTRA_STATE.value and tax_type in GENERIC_TAX_TYPES and tax.get("tax_amount", 0.0) > 0:
                half_rate = round(tax.get("tax_rate", 0.0) / 2, CURRENCY_DECIMAL_PLACES)
                half_amount = round(tax.get("tax_amount", 0.0) / 2, CURRENCY_DECIMAL_PLACES)
                normalized.extend([
                    {**tax, "tax_type": "CGST", "tax_rate": half_rate, "tax_amount": half_amount},
                    {**tax, "tax_type": "SGST", "tax_rate": half_rate, "tax_amount": half_amount},
                ])
            else:
                tax["tax_type"] = tax_type
                normalized.append(tax)
        item["taxes"] = normalized

    component_totals = {"CGST": 0.0, "SGST": 0.0, "IGST": 0.0}
    for item in data.get("line_items") or []:
        for tax in item.get("taxes") or []:
            tax_type = str(tax.get("tax_type") or "").upper()
            if tax_type in component_totals:
                component_totals[tax_type] += tax.get("tax_amount", 0.0)
    if data.get("total_cgst", 0.0) == 0.0 and component_totals["CGST"] > 0:
        data["total_cgst"] = round(component_totals["CGST"], CURRENCY_DECIMAL_PLACES)
    if data.get("total_sgst", 0.0) == 0.0 and component_totals["SGST"] > 0:
        data["total_sgst"] = round(component_totals["SGST"], CURRENCY_DECIMAL_PLACES)
    if data.get("total_igst", 0.0) == 0.0 and component_totals["IGST"] > 0:
        data["total_igst"] = round(component_totals["IGST"], CURRENCY_DECIMAL_PLACES)
    align_aggregate_taxes_with_supply_type(data, supply_type)


def align_aggregate_taxes_with_supply_type(data: dict[str, Any], supply_type: str) -> None:
    """Keep aggregate tax totals consistent with GSTIN-derived supply type."""
    total_cgst = data.get("total_cgst", 0.0)
    total_sgst = data.get("total_sgst", 0.0)
    total_igst = data.get("total_igst", 0.0)
    state_tax_total = total_cgst + total_sgst

    if supply_type == SupplyType.INTER_STATE.value and total_igst == 0.0 and state_tax_total > 0.0:
        convert_line_state_taxes_to_igst(data)
        data["total_igst"] = round(state_tax_total, CURRENCY_DECIMAL_PLACES)
        data["total_cgst"] = 0.0
        data["total_sgst"] = 0.0
    elif supply_type == SupplyType.INTRA_STATE.value and total_igst > 0.0 and state_tax_total > 0.0:
        if abs(total_igst - state_tax_total) <= MATH_TOLERANCE:
            data["total_igst"] = 0.0


def convert_line_state_taxes_to_igst(data: dict[str, Any]) -> None:
    """Convert line-level CGST/SGST taxes to IGST for inter-state invoices."""
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        taxes = [tax for tax in item.get("taxes") or [] if isinstance(tax, dict)]
        state_taxes = [tax for tax in taxes if str(tax.get("tax_type") or "").upper() in {"CGST", "SGST"}]
        other_taxes = [tax for tax in taxes if str(tax.get("tax_type") or "").upper() not in {"CGST", "SGST"}]
        if not state_taxes:
            continue
        taxable_amount = max((tax.get("taxable_amount", 0.0) for tax in state_taxes), default=item.get("taxable_value", 0.0))
        tax_amount = round(sum(tax.get("tax_amount", 0.0) for tax in state_taxes), CURRENCY_DECIMAL_PLACES)
        tax_rate = round(sum(tax.get("tax_rate", 0.0) for tax in state_taxes), CURRENCY_DECIMAL_PLACES)
        other_taxes.append({
            "tax_type": "IGST",
            "tax_rate": tax_rate,
            "taxable_amount": taxable_amount,
            "tax_amount": tax_amount,
        })
        item["taxes"] = other_taxes


def sum_line_tax(data: dict[str, Any]) -> float:
    """Return summed line-level tax amount."""
    total = 0.0
    for item in data.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        for tax in item.get("taxes") or []:
            if isinstance(tax, dict):
                total += to_float(tax.get("tax_amount"))
    return total


def supply_type_value(value: Any) -> str:
    """Return a normalized SupplyType string from enum or plain values."""
    if isinstance(value, SupplyType):
        return value.value
    text = str(value or SupplyType.UNKNOWN.value)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.upper()


def is_line_item_header(line: str) -> bool:
    """Detect the start of invoice item/tax tables."""
    lower = line.lower()
    return sum(1 for header in LINE_TABLE_HEADERS if header in lower) >= 3


def is_section_noise(line: str) -> bool:
    """Return true for labels that are not address content."""
    lower = line.strip().lower()
    return lower in {"bill to", "ship to", "shipped to"} or lower.startswith("place of supply")


def looks_like_company_name(line: str) -> bool:
    """Heuristically detect legal names in address blocks."""
    upper = line.upper()
    tokens = ("PRIVATE", "PVT", "LIMITED", "LTD", "LLP", "INC", "CORPORATION", "COMPANY")
    return any(token in upper for token in tokens)


def clean_address_segment(text: str) -> str:
    """Collapse whitespace and remove repeated label punctuation from addresses."""
    return " ".join(text.replace(" :", ":").split()).strip(" :-")


def empty_invoice(vendor_hint: str | None = None) -> dict[str, Any]:
    """Return an empty but schema-valid invoice payload."""
    return InvoiceData(vendor_name=vendor_hint, supply_type=SupplyType.UNKNOWN).model_dump()
