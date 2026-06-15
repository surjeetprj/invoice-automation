from __future__ import annotations

"""Ledger-only TallyPrime purchase voucher XML builders."""

from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from ...config import PURCHASE_LEDGER_NAME, TALLY_COMPANY
from ...domain.parsing import parse_date
from ...domain.schemas import InvoiceData, SupplyType
from ..exports.exporters import tally_tax_ledgers, tax_components_for_item
from .masters import add_text


def build_purchase_voucher_xml(invoice_id: int, data: InvoiceData) -> bytes:
    """Build a ledger-only purchase voucher import envelope."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    add_text(header, "TALLYREQUEST", "Import Data")
    body = SubElement(envelope, "BODY")
    import_data = SubElement(body, "IMPORTDATA")
    request_desc = SubElement(import_data, "REQUESTDESC")
    add_text(request_desc, "REPORTNAME", "Vouchers")
    if TALLY_COMPANY:
        static = SubElement(request_desc, "STATICVARIABLES")
        add_text(static, "SVCURRENTCOMPANY", TALLY_COMPANY)
    request_data = SubElement(import_data, "REQUESTDATA")
    tally_message = SubElement(request_data, "TALLYMESSAGE")
    voucher = SubElement(tally_message, "VOUCHER", VCHTYPE="Purchase", ACTION="Create", OBJVIEW="Accounting Voucher View")
    voucher_date = tally_date(data.date)
    add_date(voucher, "DATE", voucher_date)
    add_date(voucher, "EFFECTIVEDATE", voucher_date)
    add_text(voucher, "VOUCHERTYPENAME", "Purchase")
    add_text(voucher, "PERSISTEDVIEW", "Accounting Voucher View")
    add_text(voucher, "VOUCHERNUMBER", data.invoice_number or f"Invoice-{invoice_id}")
    add_text(voucher, "REFERENCE", data.invoice_number or f"Invoice-{invoice_id}")
    add_date(voucher, "REFERENCEDATE", voucher_date)
    add_text(voucher, "PARTYINVNO", data.invoice_number or f"Invoice-{invoice_id}")
    add_date(voucher, "PARTYINVDATE", voucher_date)
    add_text(voucher, "PARTYLEDGERNAME", data.vendor_name or "Unknown Supplier")
    if data.vendor_gstin:
        add_text(voucher, "PARTYGSTIN", data.vendor_gstin)
    add_text(voucher, "PLACEOFSUPPLY", data.place_of_supply or "")
    if can_post_detailed_gst(data):
        add_text(voucher, "GSTREGISTRATIONTYPE", "Regular")
        add_text(voucher, "GSTSUPPLYTYPE", tally_supply_type(data))
        add_text(voucher, "ISGSTOVERRIDDEN", "Yes")
        add_text(voucher, "VCHGSTSTATUSISOVERRDN", "Yes")
    add_text(voucher, "NARRATION", f"Purchase invoice {data.invoice_number or invoice_id} - {data.vendor_name or 'Unknown Supplier'}")
    add_text(voucher, "ISINVOICE", "No")
    add_ledger_entry(voucher, data.vendor_name or "Unknown Supplier", -abs(data.total_amount), deemed_positive=True)
    purchase_entry = add_ledger_entry(voucher, PURCHASE_LEDGER_NAME, data.total_taxable_amount, deemed_positive=False)
    if can_post_detailed_gst(data):
        add_purchase_gst_details(purchase_entry, data, voucher_date)
    for name, amount in tally_tax_ledgers(data):
        if amount > 0:
            tax_entry = add_ledger_entry(voucher, name, amount, deemed_positive=False)
            add_tax_ledger_gst_details(tax_entry, name, data)
    if data.round_off:
        add_ledger_entry(voucher, "Round Off", data.round_off, deemed_positive=data.round_off < 0)
    indent(envelope, space="  ")
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def add_ledger_entry(voucher: Element, ledger_name: str, amount: float, *, deemed_positive: bool) -> Element:
    """Append one ledger entry to a purchase voucher."""
    entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    add_text(entry, "LEDGERNAME", ledger_name)
    add_text(entry, "ISDEEMEDPOSITIVE", "Yes" if deemed_positive else "No")
    add_text(entry, "AMOUNT", f"{amount:.2f}")
    return entry


def add_purchase_gst_details(entry: Element, data: InvoiceData, voucher_date: str) -> None:
    """Append Tally GST override details to the purchase ledger entry."""
    if data.total_taxable_amount <= 0:
        return
    add_text(entry, "GSTOVRDNSTOREDNATURE", "Purchase Taxable")
    add_text(entry, "GSTOVRDNTAXABILITY", "Taxable")
    add_text(entry, "GSTOVRDNASSESSABLEVALUE", f"{data.total_taxable_amount:.2f}")
    add_text(entry, "GSTOVRDNISREVCHARGEAPPL", "No")
    add_text(entry, "GSTOVRDNINELIGIBLEITC", "No")
    add_text(entry, "GSTOVRDNITCTYPE", "Input")
    add_text(entry, "GSTOVRDNHSN", primary_hsn_sac(data))
    gst_details = SubElement(entry, "GSTDETAILS.LIST")
    add_text(gst_details, "APPLICABLEFROM", voucher_date)
    add_text(gst_details, "TAXABILITY", "Taxable")
    add_text(gst_details, "GSTNATUREOFSUPPLY", "Goods" if primary_hsn_sac(data) and not primary_hsn_sac(data).startswith("99") else "Services")
    add_text(gst_details, "HSNCODE", primary_hsn_sac(data))
    add_text(gst_details, "HSN", primary_hsn_sac(data))
    add_text(gst_details, "SOURCETYPE", "Stock Item")
    add_text(gst_details, "STATEWISEDETAILS.LIST", "")
    for duty_head, rate in gst_rate_details(data):
        rate_details = SubElement(gst_details, "GSTRATEDETAILS.LIST")
        add_text(rate_details, "GSTRATEDUTYHEAD", duty_head)
        add_text(rate_details, "GSTRATEVALUATIONTYPE", "Based on Value")
        add_text(rate_details, "GSTRATE", f"{rate:.2f}")
    add_gst_value_details(entry, data)


def add_gst_value_details(entry: Element, data: InvoiceData) -> None:
    """Append taxable and tax value breakup for Tally's GST detail screens."""
    details = SubElement(entry, "GSTOVRDNINELIGIBLEITC.LIST")
    add_text(details, "GSTOVRDNINELIGIBLEITC", "No")
    value_details = SubElement(entry, "GSTOVRDNSTOREDNATURE.LIST")
    add_text(value_details, "GSTOVRDNSTOREDNATURE", "Purchase Taxable")
    add_text(value_details, "GSTOVRDNASSESSABLEVALUE", f"{data.total_taxable_amount:.2f}")
    for tax_type, amount in gst_amount_details(data):
        tax_details = SubElement(entry, "GSTOVRDNTAXDETAILS.LIST")
        add_text(tax_details, "GSTOVRDNTAXTYPE", duty_head_name(tax_type))
        add_text(tax_details, "GSTOVRDNTAXAMOUNT", f"{amount:.2f}")


def add_tax_ledger_gst_details(entry: Element, ledger_name: str, data: InvoiceData) -> None:
    """Mark tax ledger entries with their GST tax type."""
    if not can_post_detailed_gst(data):
        return
    lower = ledger_name.lower()
    tax_type = ""
    if "igst" in lower:
        tax_type = "Integrated Tax"
    elif "cgst" in lower:
        tax_type = "Central Tax"
    elif "sgst" in lower or "utgst" in lower:
        tax_type = "State Tax"
    elif "cess" in lower:
        tax_type = "Cess"
    if tax_type:
        add_text(entry, "GSTOVRDNTAXTYPE", tax_type)
        add_text(entry, "GSTOVRDNASSESSABLEVALUE", f"{data.total_taxable_amount:.2f}")


def add_date(parent: Element, tag: str, value: str) -> Element:
    """Append a Tally date node with its native type hint."""
    node = SubElement(parent, tag, TYPE="Date")
    node.text = value
    return node


def primary_hsn_sac(data: InvoiceData) -> str:
    """Return the first visible HSN/SAC code from invoice line items."""
    for item in data.line_items:
        if item.hsn_sac:
            return item.hsn_sac.strip()
    return ""


def can_post_detailed_gst(data: InvoiceData) -> bool:
    """Return True when enough party data exists for Tally GST override details."""
    return bool(data.vendor_gstin and data.total_taxable_amount > 0 and gst_amount_details(data))


def gst_rate_details(data: InvoiceData) -> list[tuple[str, float]]:
    """Return Tally GST duty head/rate pairs from line taxes or totals."""
    rates: dict[str, float] = {}
    for item in data.line_items:
        components = tax_components_for_item(item)
        for tax_type, values in components.items():
            if values["amount"] > 0:
                rates[tax_type] = max(rates.get(tax_type, 0.0), values["rate"])
    if not rates and data.total_taxable_amount > 0:
        for tax_type, amount in gst_amount_details(data):
            rates[tax_type] = round((amount / data.total_taxable_amount) * 100, 2)
    return [(duty_head_name(tax_type), rate) for tax_type, rate in rates.items() if rate > 0]


def gst_amount_details(data: InvoiceData) -> list[tuple[str, float]]:
    """Return non-zero invoice-level GST component amounts."""
    details = [
        ("CGST", data.total_cgst),
        ("SGST", data.total_sgst),
        ("IGST", data.total_igst),
        ("CESS", data.total_cess),
    ]
    if any(amount > 0 for _tax_type, amount in details):
        return [(tax_type, amount) for tax_type, amount in details if amount > 0]
    amounts: dict[str, float] = {}
    for item in data.line_items:
        for tax in item.taxes:
            tax_type = tax.tax_type.upper()
            if tax_type in {"CGST", "SGST", "IGST", "CESS"}:
                amounts[tax_type] = amounts.get(tax_type, 0.0) + tax.tax_amount
    return [(tax_type, amount) for tax_type, amount in amounts.items() if amount > 0]


def duty_head_name(tax_type: str) -> str:
    """Map GST component codes to Tally duty head names."""
    return {
        "CGST": "Central Tax",
        "SGST": "State Tax",
        "IGST": "Integrated Tax",
        "CESS": "Cess",
    }.get(tax_type.upper(), tax_type)


def tally_supply_type(data: InvoiceData) -> str:
    """Return a Tally-friendly GST supply type label."""
    value = data.supply_type.value if isinstance(data.supply_type, SupplyType) else str(data.supply_type or "")
    if value == SupplyType.INTER_STATE.value:
        return "Inter-State"
    if value == SupplyType.INTRA_STATE.value:
        return "Intra-State"
    return ""


def tally_date(value: str | None) -> str:
    """Format a date value for Tally XML."""
    dt = parse_date(value)
    return (dt or datetime.now()).strftime("%Y%m%d")
