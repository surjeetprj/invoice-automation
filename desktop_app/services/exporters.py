from __future__ import annotations

"""Invoice export helpers for CSV, JSON, Tally XML, and ERPNext."""

import csv
import hashlib
import io
import json
import re
import urllib.parse
from datetime import datetime
from typing import Any
from xml.etree.ElementTree import Element, SubElement, indent, tostring

import requests

from ..config import ERPNEXT_API_KEY, ERPNEXT_API_SECRET, ERPNEXT_URL
from ..domain.parsing import parse_date
from ..domain.schemas import InvoiceData


def export_invoice_csv(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """Generate a CSV export for an invoice."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Invoice Number", "Date", "Vendor", "Vendor GSTIN", "Customer", "Customer GSTIN", "Total"])
    writer.writerow([data.invoice_number or "", data.date or "", data.vendor_name or "", data.vendor_gstin or "", data.customer_name or "", data.customer_gstin or "", data.total_amount])
    writer.writerow([])
    writer.writerow(["Sr No", "Description", "HSN/SAC", "Qty", "Unit", "Rate", "Discount", "Taxable", "Cess", "Total"])
    for item in data.line_items:
        writer.writerow([item.sr_no or "", item.description, item.hsn_sac or "", item.quantity, item.unit or "", item.rate, item.discount, item.taxable_value, item.cess_amount, item.total])
    writer.writerow([])
    writer.writerow(["Total Taxable", data.total_taxable_amount])
    writer.writerow(["CGST", data.total_cgst])
    writer.writerow(["SGST", data.total_sgst])
    writer.writerow(["IGST", data.total_igst])
    writer.writerow(["CESS", data.total_cess])
    writer.writerow(["Tax Total", data.total_tax_amount])
    writer.writerow(["Round Off", data.round_off])
    writer.writerow(["Grand Total", data.total_amount])
    return buffer.getvalue().encode("utf-8"), make_filename(invoice_id, "csv")


def export_invoice_json(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """Generate a JSON export for an invoice."""
    payload = {"invoice_id": invoice_id, "exported_at": datetime.now().isoformat(), "data": data.model_dump()}
    return json.dumps(payload, indent=2, default=str).encode("utf-8"), make_filename(invoice_id, "json")


def export_invoice_tally(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """Generate a Tally-compatible XML voucher export."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    add_text(header, "TALLYREQUEST", "Import Data")
    body = SubElement(envelope, "BODY")
    import_data = SubElement(body, "IMPORTDATA")
    request_desc = SubElement(import_data, "REQUESTDESC")
    add_text(request_desc, "REPORTNAME", "Vouchers")
    request_data = SubElement(import_data, "REQUESTDATA")
    tally_message = SubElement(request_data, "TALLYMESSAGE")
    voucher = SubElement(tally_message, "VOUCHER", VCHTYPE="Sales", ACTION="Create")
    add_text(voucher, "VOUCHERTYPENAME", "Sales")
    add_text(voucher, "DATE", tally_date(data.date))
    add_text(voucher, "VOUCHERNUMBER", data.invoice_number or "")
    add_text(voucher, "REFERENCE", data.invoice_number or "")
    add_text(voucher, "PARTYGSTIN", data.customer_gstin or "")
    add_text(voucher, "PLACEOFSUPPLY", data.place_of_supply or "")
    add_text(voucher, "NARRATION", f"Invoice {data.invoice_number or ''} - {data.vendor_name or 'Unknown Vendor'}")
    party = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    add_text(party, "LEDGERNAME", data.customer_name or "Sundry Debtors")
    add_text(party, "ISDEEMEDPOSITIVE", "Yes")
    add_text(party, "AMOUNT", f"-{data.total_amount:.2f}")
    sales = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    add_text(sales, "LEDGERNAME", "Sales Account")
    add_text(sales, "ISDEEMEDPOSITIVE", "No")
    add_text(sales, "AMOUNT", f"{data.total_taxable_amount:.2f}")
    for name, amount in (("Output CGST", data.total_cgst), ("Output SGST", data.total_sgst), ("Output IGST", data.total_igst), ("Output CESS", data.total_cess)):
        if amount > 0:
            ledger = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            add_text(ledger, "LEDGERNAME", name)
            add_text(ledger, "ISDEEMEDPOSITIVE", "No")
            add_text(ledger, "AMOUNT", f"{amount:.2f}")
    for item in data.line_items:
        inv = SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
        add_text(inv, "STOCKITEMNAME", item.description or "Unknown Item")
        add_text(inv, "ISDEEMEDPOSITIVE", "No")
        add_text(inv, "AMOUNT", f"{item.taxable_value:.2f}")
        add_text(inv, "ACTUALQTY", f"{item.quantity:.2f} {item.unit or 'NOS'}")
        add_text(inv, "BILLEDQTY", f"{item.quantity:.2f} {item.unit or 'NOS'}")
        add_text(inv, "RATE", f"{item.rate:.2f}")
        if item.hsn_sac:
            add_text(inv, "HSNCODE", item.hsn_sac)
    indent(envelope, space="  ")
    return tostring(envelope, encoding="utf-8", xml_declaration=True), make_filename(invoice_id, "xml", suffix="_tally")


def export_to_erpnext(data: InvoiceData) -> dict[str, Any]:
    """Push an invoice to ERPNext as a Purchase Invoice."""
    if not ERPNEXT_URL or not ERPNEXT_API_KEY or not ERPNEXT_API_SECRET:
        return {"success": False, "error": "ERPNext configuration is incomplete."}
    headers = {
        "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    supplier = data.vendor_name or "Unknown Supplier"
    items = []
    for index, item in enumerate(data.line_items):
        raw_name = item.description or f"Unknown Item {index + 1}"
        safe_prefix = re.sub(r"[^A-Za-z0-9]", "", raw_name.upper())[:10] or "ITEM"
        code = f"ITM-{safe_prefix}-{hashlib.md5(raw_name.encode('utf-8')).hexdigest()[:6]}"
        items.append({"item_code": code, "item_name": raw_name[:140], "qty": item.quantity or 1.0, "rate": item.rate, "uom": item.unit or "Nos"})
    payload = {
        "doctype": "Purchase Invoice",
        "supplier": supplier,
        "posting_date": erp_date(data.date),
        "due_date": erp_date(data.due_date or data.date),
        "bill_no": data.invoice_number,
        "bill_date": erp_date(data.date),
        "items": items,
    }
    try:
        response = requests.post(f"{ERPNEXT_URL.rstrip('/')}/api/resource/Purchase Invoice", json=payload, headers=headers, timeout=20)
        if response.status_code in {200, 201}:
            reference = response.json().get("data", {}).get("name")
            return {"success": True, "message": f"Successfully exported to ERPNext. Reference: {reference}", "erp_reference": reference}
        return {"success": False, "error": f"ERPNext export failed: {response.status_code} {response.text}"}
    except requests.RequestException as exc:
        return {"success": False, "error": f"Connection error: {exc}"}


def add_text(parent: Element, tag: str, text: Any) -> Element:
    """Append a text child node to an XML element."""
    node = SubElement(parent, tag)
    node.text = str(text)
    return node


def make_filename(invoice_id: int, ext: str, suffix: str = "") -> str:
    """Create a timestamped export filename."""
    return f"invoice_{invoice_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}.{ext}"


def tally_date(value: str | None) -> str:
    """Format a date value for Tally XML."""
    dt = parse_date(value)
    return (dt or datetime.now()).strftime("%Y%m%d")


def erp_date(value: str | None) -> str:
    """Format a date value for ERPNext."""
    dt = parse_date(value)
    return (dt or datetime.now()).strftime("%Y-%m-%d")

