from __future__ import annotations

"""Invoice export helpers for JSON and Tally XML."""

import json
from datetime import datetime
from typing import Any
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from ...config import (
    INPUT_CESS_LEDGER_NAME,
    INPUT_CGST_LEDGER_NAME,
    INPUT_IGST_LEDGER_NAME,
    INPUT_SGST_LEDGER_NAME,
    PURCHASE_LEDGER_NAME,
)
from ...domain.parsing import parse_date
from ...domain.schemas import InvoiceData


def export_invoice_json(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """Generate a JSON export for an invoice."""
    payload = {
        "invoice_id": invoice_id,
        "exported_at": datetime.now().isoformat(),
        "data": data.model_dump(exclude={"line_items": {"__all__": {"item_name"}}}),
    }
    return json.dumps(payload, indent=2, default=str).encode("utf-8"), make_filename(invoice_id, "json")


def export_invoice_tally(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """Generate a Tally-compatible purchase voucher XML export."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    add_text(header, "TALLYREQUEST", "Import Data")
    body = SubElement(envelope, "BODY")
    import_data = SubElement(body, "IMPORTDATA")
    request_desc = SubElement(import_data, "REQUESTDESC")
    add_text(request_desc, "REPORTNAME", "Vouchers")
    request_data = SubElement(import_data, "REQUESTDATA")
    tally_message = SubElement(request_data, "TALLYMESSAGE")
    voucher = SubElement(tally_message, "VOUCHER", VCHTYPE="Purchase", ACTION="Create")
    add_text(voucher, "VOUCHERTYPENAME", "Purchase")
    add_text(voucher, "DATE", tally_date(data.date))
    add_text(voucher, "VOUCHERNUMBER", data.invoice_number or "")
    add_text(voucher, "REFERENCE", data.invoice_number or "")
    add_text(voucher, "PARTYLEDGERNAME", data.vendor_name or "Unknown Supplier")
    add_text(voucher, "PARTYGSTIN", data.vendor_gstin or "")
    add_text(voucher, "PLACEOFSUPPLY", data.place_of_supply or "")
    add_text(voucher, "NARRATION", f"Purchase invoice {data.invoice_number or ''} - {data.vendor_name or 'Unknown Supplier'}")
    party = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    add_text(party, "LEDGERNAME", data.vendor_name or "Sundry Creditors")
    add_text(party, "ISDEEMEDPOSITIVE", "Yes")
    add_text(party, "AMOUNT", f"-{data.total_amount:.2f}")
    purchase = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    add_text(purchase, "LEDGERNAME", PURCHASE_LEDGER_NAME)
    add_text(purchase, "ISDEEMEDPOSITIVE", "No")
    add_text(purchase, "AMOUNT", f"{data.total_taxable_amount:.2f}")
    for name, amount in tally_tax_ledgers(data):
        if amount > 0:
            ledger = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            add_text(ledger, "LEDGERNAME", name)
            add_text(ledger, "ISDEEMEDPOSITIVE", "No")
            add_text(ledger, "AMOUNT", f"{amount:.2f}")
    for item in data.line_items:
        inv = SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
        add_text(inv, "STOCKITEMNAME", item.description or "Unknown Item")
        add_text(inv, "ISDEEMEDPOSITIVE", "Yes")
        add_text(inv, "AMOUNT", f"{item.taxable_value:.2f}")
        add_text(inv, "ACTUALQTY", f"{item.quantity:.2f} {item.unit or 'NOS'}")
        add_text(inv, "BILLEDQTY", f"{item.quantity:.2f} {item.unit or 'NOS'}")
        add_text(inv, "RATE", f"{item.rate:.2f}")
        if item.hsn_sac:
            add_text(inv, "HSNCODE", item.hsn_sac)
    indent(envelope, space="  ")
    return tostring(envelope, encoding="utf-8", xml_declaration=True), make_filename(invoice_id, "xml", suffix="_tally")


def add_text(parent: Element, tag: str, text: Any) -> Element:
    """Append a text child node to an XML element."""
    node = SubElement(parent, tag)
    node.text = str(text)
    return node

def tax_components_for_item(item) -> dict[str, dict[str, float]]:
    """Return CGST/SGST/IGST tax rate and amount totals for one line item."""
    components = {
        "CGST": {"rate": 0.0, "amount": 0.0},
        "SGST": {"rate": 0.0, "amount": 0.0},
        "IGST": {"rate": 0.0, "amount": 0.0},
    }
    for tax in item.taxes:
        tax_type = tax.tax_type.upper()
        if tax_type in components:
            components[tax_type]["rate"] = tax.tax_rate
            components[tax_type]["amount"] += tax.tax_amount
    return components


def invoice_tax_totals(data: InvoiceData) -> dict[str, float]:
    """Return invoice-level GST component totals, preferring aggregate fields."""
    totals = {
        "CGST": data.total_cgst,
        "SGST": data.total_sgst,
        "IGST": data.total_igst,
        "CESS": data.total_cess,
    }
    if any(amount > 0 for amount in totals.values()):
        return totals
    for item in data.line_items:
        for tax in item.taxes:
            tax_type = tax.tax_type.upper()
            if tax_type in totals:
                totals[tax_type] += tax.tax_amount
        totals["CESS"] += item.cess_amount
    return totals


def tally_tax_ledgers(data: InvoiceData) -> tuple[tuple[str, float], ...]:
    """Return Tally input tax ledger names and amounts."""
    totals = invoice_tax_totals(data)
    return (
        (INPUT_CGST_LEDGER_NAME, totals["CGST"]),
        (INPUT_SGST_LEDGER_NAME, totals["SGST"]),
        (INPUT_IGST_LEDGER_NAME, totals["IGST"]),
        (INPUT_CESS_LEDGER_NAME, totals["CESS"]),
    )


def make_filename(invoice_id: int, ext: str, suffix: str = "") -> str:
    """Create a timestamped export filename."""
    return f"invoice_{invoice_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}.{ext}"


def tally_date(value: str | None) -> str:
    """Format a date value for Tally XML."""
    dt = parse_date(value)
    return (dt or datetime.now()).strftime("%Y%m%d")
