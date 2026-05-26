"""
Tally Prime XML Exporter — Generate Tally-compatible import XML.

Produces XML in Tally Prime's ENVELOPE format for importing
Sales vouchers with GST tax ledger entries.

All exports are generated in-memory and streamed directly to the client.
No files are written to disk.

Supports:
- CGST + SGST (intra-state) and IGST (inter-state) ledger entries
- Stock item entries for each line item
- Bank allocation details
- GST-specific tags: GSTIN, Place of Supply, HSN, tax rates
"""
import logging
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring, indent

from schemas import InvoiceData

logger = logging.getLogger(__name__)


def _make_filename(invoice_id: int) -> str:
    """Generate a timestamped Tally XML filename."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"invoice_{invoice_id}_{ts}_tally.xml"


def _tally_date(date_str: str | None) -> str:
    """
    Convert a date string to Tally's YYYYMMDD format.
    Falls back to today's date if parsing fails.
    """
    if not date_str:
        return datetime.now().strftime("%Y%m%d")

    # Try common Indian date formats
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y",
                "%d %b %Y", "%d %B %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y%m%d")
        except ValueError:
            continue

    return datetime.now().strftime("%Y%m%d")


def _add_text_element(parent: Element, tag: str, text: str):
    """Helper to add a child element with text content."""
    el = SubElement(parent, tag)
    el.text = str(text)
    return el


def export_invoice_tally(invoice_id: int, data: InvoiceData) -> tuple[bytes, str]:
    """
    Generate Tally Prime compatible XML for a sales voucher in memory.

    Returns:
        Tuple of (xml_bytes, filename) for streaming to client.

    XML Structure:
    <ENVELOPE>
      <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
      </HEADER>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>Vouchers</REPORTNAME>
            <STATICVARIABLES>...</STATICVARIABLES>
          </REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE>
              <VOUCHER>
                (voucher details)
              </VOUCHER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>
    """
    filename = _make_filename(invoice_id)

    # Determine supply type for tax handling
    supply_type = data.supply_type.value if data.supply_type else "UNKNOWN"
    is_intra_state = supply_type == "INTRA_STATE"

    tally_date = _tally_date(data.date)

    # ── Build XML ─────────────────────────────
    envelope = Element("ENVELOPE")

    # Header
    header = SubElement(envelope, "HEADER")
    _add_text_element(header, "TALLYREQUEST", "Import Data")

    # Body
    body = SubElement(envelope, "BODY")
    import_data = SubElement(body, "IMPORTDATA")

    # Request Description
    req_desc = SubElement(import_data, "REQUESTDESC")
    _add_text_element(req_desc, "REPORTNAME", "Vouchers")
    static_vars = SubElement(req_desc, "STATICVARIABLES")
    _add_text_element(static_vars, "SVCURRENTCOMPANY", "##SVCURRENTCOMPANY")

    # Request Data
    req_data = SubElement(import_data, "REQUESTDATA")
    tally_msg = SubElement(req_data, "TALLYMESSAGE", xmlns_UDF="TallyUDF")

    # ── Voucher ──────────────────────────────
    voucher = SubElement(tally_msg, "VOUCHER", VCHTYPE="Sales", ACTION="Create")
    _add_text_element(voucher, "VOUCHERTYPENAME", "Sales")
    _add_text_element(voucher, "DATE", tally_date)
    _add_text_element(voucher, "REFERENCE", data.invoice_number or "")
    _add_text_element(voucher, "VOUCHERNUMBER", data.invoice_number or "")
    _add_text_element(voucher, "NARRATION",
                      f"Invoice #{data.invoice_number or 'N/A'} - "
                      f"{data.vendor_name or 'Unknown Vendor'}")

    # GST Registration details
    _add_text_element(voucher, "PARTYGSTIN", data.customer_gstin or "")
    _add_text_element(voucher, "PLACEOFSUPPLY", data.place_of_supply or "")
    _add_text_element(voucher, "GSTTYPEOFSUPPLY",
                      "Intra State" if is_intra_state else "Inter State")

    if data.irn:
        _add_text_element(voucher, "IRN", data.irn)
    if data.ack_number:
        _add_text_element(voucher, "IRNAUTHDATE", data.ack_date or "")
        _add_text_element(voucher, "IRNACKNO", data.ack_number)

    if data.e_way_bill_no:
        _add_text_element(voucher, "EABORDEWAYBILLNO", data.e_way_bill_no)

    _add_text_element(voucher, "ISGSTREGISTERED", "Yes")
    _add_text_element(voucher, "ISREVERSECHARGEAPPLICABLE",
                      "Yes" if data.reverse_charge == "Y" else "No")

    # ── Party (Customer / Buyer) Ledger Entry ──
    party_entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    _add_text_element(party_entry, "LEDGERNAME", data.customer_name or "Sundry Debtors")
    _add_text_element(party_entry, "ISDEEMEDPOSITIVE", "Yes")
    _add_text_element(party_entry, "AMOUNT", f"-{data.total_amount:.2f}")

    # ── Sales Ledger Entry ────────────────────
    sales_entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    _add_text_element(sales_entry, "LEDGERNAME", "Sales Account")
    _add_text_element(sales_entry, "ISDEEMEDPOSITIVE", "No")
    _add_text_element(sales_entry, "AMOUNT", f"{data.total_taxable_amount:.2f}")

    # ── Tax Ledger Entries ────────────────────
    if is_intra_state:
        # CGST Ledger
        if data.total_cgst > 0:
            cgst_entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _add_text_element(cgst_entry, "LEDGERNAME", "Output CGST")
            _add_text_element(cgst_entry, "ISDEEMEDPOSITIVE", "No")
            _add_text_element(cgst_entry, "AMOUNT", f"{data.total_cgst:.2f}")

        # SGST Ledger
        if data.total_sgst > 0:
            sgst_entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _add_text_element(sgst_entry, "LEDGERNAME", "Output SGST")
            _add_text_element(sgst_entry, "ISDEEMEDPOSITIVE", "No")
            _add_text_element(sgst_entry, "AMOUNT", f"{data.total_sgst:.2f}")
    else:
        # IGST Ledger
        if data.total_igst > 0:
            igst_entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            _add_text_element(igst_entry, "LEDGERNAME", "Output IGST")
            _add_text_element(igst_entry, "ISDEEMEDPOSITIVE", "No")
            _add_text_element(igst_entry, "AMOUNT", f"{data.total_igst:.2f}")

    # CESS Ledger (if applicable)
    if data.total_cess > 0:
        cess_entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        _add_text_element(cess_entry, "LEDGERNAME", "Output CESS")
        _add_text_element(cess_entry, "ISDEEMEDPOSITIVE", "No")
        _add_text_element(cess_entry, "AMOUNT", f"{data.total_cess:.2f}")

    # Round-off Ledger (if applicable)
    if data.round_off != 0:
        roundoff_entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        _add_text_element(roundoff_entry, "LEDGERNAME", "Round Off")
        _add_text_element(roundoff_entry, "ISDEEMEDPOSITIVE",
                          "Yes" if data.round_off < 0 else "No")
        _add_text_element(roundoff_entry, "AMOUNT", f"{data.round_off:.2f}")

    # ── Stock Item Entries (Line Items) ───────
    for item in data.line_items:
        inv_entry = SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
        _add_text_element(inv_entry, "STOCKITEMNAME", item.description or "Unknown Item")
        _add_text_element(inv_entry, "ISDEEMEDPOSITIVE", "No")
        _add_text_element(inv_entry, "AMOUNT", f"{item.taxable_value:.2f}")
        _add_text_element(inv_entry, "ACTUALQTY", f"{item.quantity:.2f} {item.unit or 'NOS'}")
        _add_text_element(inv_entry, "BILLEDQTY", f"{item.quantity:.2f} {item.unit or 'NOS'}")
        _add_text_element(inv_entry, "RATE", f"{item.rate:.2f}")

        if item.discount > 0:
            _add_text_element(inv_entry, "DISCOUNT", f"{item.discount:.2f}")

        # HSN/SAC details
        if item.hsn_sac:
            _add_text_element(inv_entry, "HSNCODE", item.hsn_sac)

        # GST rate details per item
        for tax in item.taxes:
            tt = tax.tax_type.upper()
            if tt == "CGST":
                _add_text_element(inv_entry, "GSTRATE", f"{tax.tax_rate * 2:.2f}")
                break
            elif tt == "IGST":
                _add_text_element(inv_entry, "GSTRATE", f"{tax.tax_rate:.2f}")
                break

    # ── Serialize XML ─────────────────────────
    indent(envelope, space="  ")
    xml_content = tostring(envelope, encoding="unicode", xml_declaration=True)
    content = xml_content.encode("utf-8")

    logger.info("Tally XML export generated in-memory: %s (%d bytes)", filename, len(content))
    return content, filename
