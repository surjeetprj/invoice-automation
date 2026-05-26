"""
Exporter — Generate downloadable CSV and JSON content for ERP integration.

All exports are generated in-memory and streamed directly to the client.
No files are written to disk.

Enhanced with:
- Tax-type-specific columns (CGST, SGST, IGST, CESS)
- Discount and HSN/SAC columns
- Round-off and supply type in header
- Full invoice metadata in JSON export
"""
import csv
import io
import json
import logging
from datetime import datetime

from schemas import InvoiceData

logger = logging.getLogger(__name__)


def _make_filename(invoice_id: int, ext: str) -> str:
    """Generate a timestamped export filename."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"invoice_{invoice_id}_{ts}.{ext}"


def _get_line_item_taxes(item) -> dict:
    """Extract per-tax-type amounts from a line item's tax list."""
    taxes = {
        "cgst_rate": 0.0, "cgst_amt": 0.0,
        "sgst_rate": 0.0, "sgst_amt": 0.0,
        "igst_rate": 0.0, "igst_amt": 0.0,
        "cess_amt": 0.0,
    }
    for tax in item.taxes:
        tt = tax.tax_type.upper()
        if tt == "CGST":
            taxes["cgst_rate"] = tax.tax_rate
            taxes["cgst_amt"] = tax.tax_amount
        elif tt == "SGST":
            taxes["sgst_rate"] = tax.tax_rate
            taxes["sgst_amt"] = tax.tax_amount
        elif tt == "IGST":
            taxes["igst_rate"] = tax.tax_rate
            taxes["igst_amt"] = tax.tax_amount
        elif tt == "CESS":
            taxes["cess_amt"] += tax.tax_amount

    taxes["cess_amt"] += item.cess_amount
    return taxes


def export_invoice_csv(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """
    Generate invoice CSV content in memory.

    Returns:
        Tuple of (csv_bytes, filename) for streaming to client.

    CSV structure:
        - Header section: invoice-level metadata
        - Line items with tax-type-specific columns
        - Tax summary footer
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    # ── Header section ────────────────────
    writer.writerow([
        "Invoice Number", "Date", "Supply Type", "Reverse Charge",
        "Vendor Name", "Vendor GSTIN", "Customer Name", "Customer GSTIN",
        "Place of Supply", "E-Way Bill No.",
    ])
    writer.writerow([
        data.invoice_number or "",
        data.date or "",
        data.supply_type.value if data.supply_type else "",
        data.reverse_charge or "",
        data.vendor_name or "",
        data.vendor_gstin or "",
        data.customer_name or "",
        data.customer_gstin or "",
        data.place_of_supply or "",
        data.e_way_bill_no or "",
    ])
    writer.writerow([])  # blank separator

    # ── E-invoicing section (if present) ──
    if data.irn:
        writer.writerow(["IRN", data.irn])
        if data.ack_number:
            writer.writerow(["Ack Number", data.ack_number, "Ack Date", data.ack_date or ""])
        writer.writerow([])

    # ── Line items section ────────────────
    writer.writerow([
        "Sr.No", "Description", "HSN/SAC", "Qty", "Unit", "Rate",
        "Discount", "Taxable Value",
        "CGST %", "CGST Amt", "SGST %", "SGST Amt",
        "IGST %", "IGST Amt", "CESS Amt", "Total",
    ])
    for item in data.line_items:
        taxes = _get_line_item_taxes(item)
        writer.writerow([
            item.sr_no or "",
            item.description,
            item.hsn_sac or "",
            item.quantity,
            item.unit or "",
            item.rate,
            item.discount,
            item.taxable_value,
            taxes["cgst_rate"], taxes["cgst_amt"],
            taxes["sgst_rate"], taxes["sgst_amt"],
            taxes["igst_rate"], taxes["igst_amt"],
            taxes["cess_amt"],
            item.total,
        ])

    writer.writerow([])  # blank separator

    # ── Tax summary footer ────────────────
    writer.writerow(["Total Taxable Amount", data.total_taxable_amount])
    writer.writerow(["Total CGST", data.total_cgst])
    writer.writerow(["Total SGST", data.total_sgst])
    writer.writerow(["Total IGST", data.total_igst])
    writer.writerow(["Total CESS", data.total_cess])
    writer.writerow(["Total Tax Amount", data.total_tax_amount])
    writer.writerow(["Round Off", data.round_off])
    writer.writerow(["Grand Total", data.total_amount])

    if data.amount_in_words:
        writer.writerow(["Amount in Words", data.amount_in_words])

    # ── Bank details ──────────────────────
    if data.bank_name:
        writer.writerow([])
        parts = [f"Bank: {data.bank_name}"]
        if data.account_no:
            parts.append(f"A/c: {data.account_no}")
        if data.ifsc:
            parts.append(f"IFSC: {data.ifsc}")
        if data.branch:
            parts.append(f"Branch: {data.branch}")
        writer.writerow(["Bank Details", " | ".join(parts)])

    content = buffer.getvalue().encode("utf-8")
    filename = _make_filename(invoice_id, "csv")
    logger.info("CSV export generated in-memory: %s (%d bytes)", filename, len(content))
    return content, filename


def export_invoice_json(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """
    Generate invoice JSON content in memory.

    Returns:
        Tuple of (json_bytes, filename) for streaming to client.

    Includes all fields: supply type, reverse charge, e-invoicing,
    shipping, tax breakup, and bank details.
    """
    payload = {
        "invoice_id": invoice_id,
        "exported_at": datetime.now().isoformat(),
        "data": data.model_dump(),
    }

    content = json.dumps(payload, indent=2, default=str).encode("utf-8")
    filename = _make_filename(invoice_id, "json")
    logger.info("JSON export generated in-memory: %s (%d bytes)", filename, len(content))
    return content, filename
