from __future__ import annotations

"""Ledger-only and inventory-based TallyPrime purchase voucher XML builders."""

from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from ..settings import get_tally_settings
from .mapping import (
    INPUT_CESS_LEDGER as MAP_INPUT_CESS_LEDGER,
    INPUT_CGST_LEDGER as MAP_INPUT_CGST_LEDGER,
    INPUT_IGST_LEDGER as MAP_INPUT_IGST_LEDGER,
    INPUT_SGST_LEDGER as MAP_INPUT_SGST_LEDGER,
    PURCHASE_LEDGER as MAP_PURCHASE_LEDGER,
    ROUND_OFF_LEDGER as MAP_ROUND_OFF_LEDGER,
    UNIT as MAP_UNIT,
    VENDOR_LEDGER as MAP_VENDOR_LEDGER,
    mapped_default,
    mapped_value,
)
from ...domain.parsing import parse_date
from ...domain.schemas import InvoiceData, SupplyType
from ..exports.exporters import invoice_tax_totals, tax_components_for_item
from ..parsing.invoice_normalizer import clean_item_description
from .masters import (
    PURCHASE_VOUCHER_TYPE,
    TALLY_NOT_APPLICABLE,
    add_text,
    address_lines,
    normalize_unit_name,
    pincode_from_address,
    stock_item_name_from_line_item,
    vendor_state,
)

ACCOUNTING_VOUCHER_VIEW = "Accounting Voucher View"
INVOICE_VOUCHER_VIEW = "Invoice Voucher View"
ITEM_INVOICE_MODE = "Item Invoice"
DEFAULT_GODOWN = "Main Location"
DEFAULT_BATCH = "Primary Batch"


def build_purchase_voucher_xml(invoice_id: int, data: InvoiceData) -> bytes:
    """Build a ledger-only Purchase voucher import envelope."""
    envelope, voucher = build_voucher_envelope(invoice_id, data, objview=ACCOUNTING_VOUCHER_VIEW)
    add_text(voucher, "PERSISTEDVIEW", ACCOUNTING_VOUCHER_VIEW)
    add_text(voucher, "ISINVOICE", "No")
    add_ledger_entry(voucher, vendor_display_name(data), -abs(data.total_amount), deemed_positive=True)
    purchase_entry = add_ledger_entry(voucher, mapped_default(MAP_PURCHASE_LEDGER, get_tally_settings().purchase_ledger_name), data.total_taxable_amount, deemed_positive=False)
    if can_post_detailed_gst(data):
        add_purchase_gst_details(purchase_entry, data, tally_date(data.date))
    for name, amount in tally_tax_ledgers(data):
        if amount > 0:
            tax_entry = add_ledger_entry(voucher, name, amount, deemed_positive=False)
            add_tax_ledger_gst_details(tax_entry, name, data)
    if data.round_off:
        add_ledger_entry(voucher, mapped_default(MAP_ROUND_OFF_LEDGER, "Round Off"), data.round_off, deemed_positive=data.round_off < 0)
    indent(envelope, space="  ")
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def build_inventory_purchase_voucher_xml(invoice_id: int, data: InvoiceData) -> bytes:
    """Build an item-wise/inventory Purchase voucher import envelope."""
    envelope, voucher = build_voucher_envelope(invoice_id, data, objview=INVOICE_VOUCHER_VIEW)
    add_text(voucher, "PERSISTEDVIEW", INVOICE_VOUCHER_VIEW)
    add_text(voucher, "ISINVOICE", "Yes")
    add_text(voucher, "VCHENTRYMODE", ITEM_INVOICE_MODE)
    add_text(voucher, "PARTYNAME", vendor_display_name(data))
    add_text(voucher, "BASICBASEPARTYNAME", vendor_display_name(data))
    add_text(voucher, "PARTYMAILINGNAME", vendor_display_name(data))
    company = get_tally_settings().tally_company
    if company:
        add_text(voucher, "BASICBUYERNAME", company)
        add_text(voucher, "CONSIGNEEMAILINGNAME", company)
        add_text(voucher, "CONSIGNEECOUNTRYNAME", "India")
    state_name = vendor_state(data)
    if state_name:
        add_text(voucher, "STATENAME", state_name)
    if data.vendor_gstin or data.vendor_address:
        add_text(voucher, "COUNTRYOFRESIDENCE", "India")
    pincode = pincode_from_address(data.vendor_address)
    if pincode:
        add_text(voucher, "PARTYPINCODE", pincode)
    mailing_lines = address_lines(data.vendor_address)
    if mailing_lines:
        list_node = SubElement(voucher, "ADDRESS.LIST", TYPE="String")
        for line in mailing_lines:
            add_text(list_node, "ADDRESS", line)

    for item in data.line_items:
        inventory_entry = SubElement(voucher, "ALLINVENTORYENTRIES.LIST")
        stock_item_name = stock_item_name_from_line_item(item)
        unit_name = tally_unit_text(item.unit)
        add_text(inventory_entry, "STOCKITEMNAME", stock_item_name)
        cleaned_desc = clean_item_description(item.item_name, item.description)
        if cleaned_desc:
            add_text(inventory_entry, "DESCRIPTION", cleaned_desc)
            
            # Additional User Description tags for TallyPrime compatibility
            bud_list = SubElement(inventory_entry, "BASICUSERDESCRIPTION.LIST", TYPE="String")
            for line in cleaned_desc.splitlines():
                stripped = line.strip()
                if stripped:
                    add_text(bud_list, "BASICUSERDESCRIPTION", stripped)
            
            # Additional Description tags for alternative TDL structures
            addl_list = SubElement(inventory_entry, "ADDLDESCRIPTION.LIST", TYPE="String")
            for line in cleaned_desc.splitlines():
                stripped = line.strip()
                if stripped:
                    add_text(addl_list, "ADDLDESCRIPTION", stripped)
        add_text(inventory_entry, "GSTOVRDNTAXABILITY", "Taxable")
        add_text(inventory_entry, "GSTSOURCETYPE", "Stock Item")
        add_text(inventory_entry, "GSTITEMSOURCE", stock_item_name)
        add_text(inventory_entry, "HSNSOURCETYPE", "Stock Item")
        add_text(inventory_entry, "HSNITEMSOURCE", stock_item_name)
        add_text(inventory_entry, "GSTOVRDNTYPEOFSUPPLY", item_supply_type(item))
        add_text(inventory_entry, "GSTRATEINFERAPPLICABILITY", "As per Masters/Company")
        add_text(inventory_entry, "GSTHSNINFERAPPLICABILITY", "As per Masters/Company")
        add_text(inventory_entry, "ISDEEMEDPOSITIVE", "Yes")
        add_text(inventory_entry, "AMOUNT", f"-{abs(item.taxable_value):.2f}")
        add_text(inventory_entry, "ACTUALQTY", quantity_text(item.quantity, unit_name))
        add_text(inventory_entry, "BILLEDQTY", quantity_text(item.quantity, unit_name))
        add_text(inventory_entry, "RATE", rate_text(item.rate, unit_name))
        if item.hsn_sac:
            hsn_code = item.hsn_sac.strip()
            add_text(inventory_entry, "HSNCODE", hsn_code)
            add_text(inventory_entry, "GSTHSNNAME", hsn_code)
        batch = SubElement(inventory_entry, "BATCHALLOCATIONS.LIST")
        add_text(batch, "GODOWNNAME", DEFAULT_GODOWN)
        add_text(batch, "BATCHNAME", DEFAULT_BATCH)
        add_text(batch, "AMOUNT", f"-{abs(item.taxable_value):.2f}")
        add_text(batch, "ACTUALQTY", quantity_text(item.quantity, unit_name))
        add_text(batch, "BILLEDQTY", quantity_text(item.quantity, unit_name))
        allocation = SubElement(inventory_entry, "ACCOUNTINGALLOCATIONS.LIST")
        add_text(allocation, "LEDGERNAME", mapped_default(MAP_PURCHASE_LEDGER, get_tally_settings().purchase_ledger_name))
        add_text(allocation, "ISDEEMEDPOSITIVE", "Yes")
        add_text(allocation, "AMOUNT", f"-{abs(item.taxable_value):.2f}")
        for duty_head, rate in item_gst_rate_details(item, data):
            rate_details = SubElement(inventory_entry, "RATEDETAILS.LIST")
            add_text(rate_details, "GSTRATEDUTYHEAD", duty_head)
            if rate > 0:
                add_text(rate_details, "GSTRATEVALUATIONTYPE", "Based on Value")
                add_text(rate_details, "GSTRATE", f"{rate:g}")
            elif duty_head == "Cess":
                add_text(rate_details, "GSTRATEVALUATIONTYPE", TALLY_NOT_APPLICABLE)
            else:
                add_text(rate_details, "GSTRATEVALUATIONTYPE", "Based on Value")
    add_item_party_ledger_entry(voucher, data, invoice_id)
    for name, amount in tally_tax_ledgers(data):
        if amount > 0:
            tax_entry = add_item_tax_ledger_entry(voucher, name, amount)
            add_tax_ledger_gst_details(tax_entry, name, data, item_mode=True)
    if data.round_off:
        add_item_tax_ledger_entry(voucher, mapped_default(MAP_ROUND_OFF_LEDGER, "Round Off"), abs(data.round_off), deemed_positive=data.round_off >= 0)
    indent(envelope, space="  ")
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def build_voucher_envelope(invoice_id: int, data: InvoiceData, *, objview: str) -> tuple[Element, Element]:
    """Create a standard purchase voucher envelope and return its voucher node."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    add_text(header, "TALLYREQUEST", "Import Data")
    body = SubElement(envelope, "BODY")
    import_data = SubElement(body, "IMPORTDATA")
    request_desc = SubElement(import_data, "REQUESTDESC")
    add_text(request_desc, "REPORTNAME", "Vouchers")
    company = get_tally_settings().tally_company
    if company:
        static = SubElement(request_desc, "STATICVARIABLES")
        add_text(static, "SVCURRENTCOMPANY", company)
    request_data = SubElement(import_data, "REQUESTDATA")
    tally_message = SubElement(request_data, "TALLYMESSAGE")
    voucher = SubElement(tally_message, "VOUCHER", VCHTYPE=PURCHASE_VOUCHER_TYPE, ACTION="Create", OBJVIEW=objview)
    voucher_date = tally_date(data.date)
    add_date(voucher, "DATE", voucher_date)
    add_date(voucher, "EFFECTIVEDATE", voucher_date)
    add_text(voucher, "VOUCHERTYPENAME", PURCHASE_VOUCHER_TYPE)
    add_text(voucher, "VOUCHERNUMBER", invoice_reference(data, invoice_id))
    add_text(voucher, "REFERENCE", invoice_reference(data, invoice_id))
    add_date(voucher, "REFERENCEDATE", voucher_date)
    add_text(voucher, "PARTYINVNO", invoice_reference(data, invoice_id))
    add_date(voucher, "PARTYINVDATE", voucher_date)
    add_text(voucher, "PARTYLEDGERNAME", vendor_display_name(data))
    if data.vendor_gstin:
        add_text(voucher, "PARTYGSTIN", data.vendor_gstin)
    add_text(voucher, "PLACEOFSUPPLY", data.place_of_supply or "")
    if objview != INVOICE_VOUCHER_VIEW and can_post_detailed_gst(data):
        add_text(voucher, "GSTREGISTRATIONTYPE", "Regular")
        add_text(voucher, "GSTSUPPLYTYPE", tally_supply_type(data))
        add_text(voucher, "ISGSTOVERRIDDEN", "Yes")
        add_text(voucher, "VCHGSTSTATUSISOVERRDN", "Yes")
    add_text(voucher, "NARRATION", f"Purchase invoice {invoice_reference(data, invoice_id)} - {vendor_display_name(data)}")
    return envelope, voucher


def add_ledger_entry(voucher: Element, ledger_name: str, amount: float, *, deemed_positive: bool) -> Element:
    """Append one ledger entry to a purchase voucher."""
    entry = SubElement(voucher, "ALLLEDGERENTRIES.LIST")
    add_text(entry, "LEDGERNAME", ledger_name)
    add_text(entry, "ISDEEMEDPOSITIVE", "Yes" if deemed_positive else "No")
    add_text(entry, "AMOUNT", f"{amount:.2f}")
    return entry


def add_item_party_ledger_entry(voucher: Element, data: InvoiceData, invoice_id: int) -> Element:
    """Append the party ledger entry Tally expects for an item invoice."""
    entry = SubElement(voucher, "LEDGERENTRIES.LIST")
    add_text(entry, "LEDGERNAME", vendor_display_name(data))
    add_text(entry, "ISDEEMEDPOSITIVE", "No")
    add_text(entry, "ISPARTYLEDGER", "Yes")
    add_text(entry, "AMOUNT", f"{abs(data.total_amount):.2f}")
    bill = SubElement(entry, "BILLALLOCATIONS.LIST")
    add_text(bill, "NAME", invoice_reference(data, invoice_id))
    add_text(bill, "BILLTYPE", "New Ref")
    add_text(bill, "AMOUNT", f"{abs(data.total_amount):.2f}")
    return entry


def add_item_tax_ledger_entry(voucher: Element, ledger_name: str, amount: float, *, deemed_positive: bool = True) -> Element:
    """Append a duty/round-off ledger line for an item invoice."""
    entry = SubElement(voucher, "LEDGERENTRIES.LIST")
    add_text(entry, "LEDGERNAME", ledger_name)
    add_text(entry, "ISDEEMEDPOSITIVE", "Yes" if deemed_positive else "No")
    add_text(entry, "AMOUNT", f"-{abs(amount):.2f}" if deemed_positive else f"{abs(amount):.2f}")
    return entry



def tally_tax_ledgers(data: InvoiceData) -> tuple[tuple[str, float], ...]:
    """Return runtime-configured Tally input tax ledger names and amounts."""
    settings = get_tally_settings()
    totals = invoice_tax_totals(data)
    return (
        (mapped_default(MAP_INPUT_CGST_LEDGER, settings.input_cgst_ledger_name), totals["CGST"]),
        (mapped_default(MAP_INPUT_SGST_LEDGER, settings.input_sgst_ledger_name), totals["SGST"]),
        (mapped_default(MAP_INPUT_IGST_LEDGER, settings.input_igst_ledger_name), totals["IGST"]),
        (mapped_default(MAP_INPUT_CESS_LEDGER, settings.input_cess_ledger_name), totals["CESS"]),
    )
def invoice_reference(data: InvoiceData, invoice_id: int) -> str:
    """Return the invoice reference used across Tally voucher fields."""
    return data.invoice_number or f"Invoice-{invoice_id}"


def vendor_display_name(data: InvoiceData) -> str:
    """Return the party ledger name used across Tally voucher fields."""
    source = data.vendor_name or "Unknown Supplier"
    return mapped_value(MAP_VENDOR_LEDGER, source, source)


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


def add_tax_ledger_gst_details(entry: Element, ledger_name: str, data: InvoiceData, *, item_mode: bool = False) -> None:
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
        if not item_mode:
            add_text(entry, "GSTOVRDNTAXTYPE", tax_type)
            add_text(entry, "GSTOVRDNASSESSABLEVALUE", f"{data.total_taxable_amount:.2f}")
        else:
            add_text(entry, "ADDLALLOCTYPE", "Appropriate by condition")


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


def quantity_text(quantity: float, unit_name: str) -> str:
    """Return a Tally quantity string."""
    quantity_text_value = f"{quantity:.2f}"
    if float(quantity).is_integer():
        quantity_text_value = str(int(quantity))
    return f"{quantity_text_value} {unit_name}".strip()


def rate_text(rate: float, unit_name: str) -> str:
    """Return a Tally rate string."""
    return f"{rate:.2f}/{unit_name}" if unit_name else f"{rate:.2f}"


def tally_unit_text(unit: str | None) -> str:
    """Return a Tally-friendly display unit for inventory rows."""
    normalized = normalize_unit_name(unit) or ""
    if not normalized:
        return ""
    mapped = mapped_value(MAP_UNIT, normalized, "")
    if mapped:
        return mapped
    return normalized.title()


def item_supply_type(item) -> str:
    """Infer goods/services for one reviewed line item."""
    hsn = (item.hsn_sac or "").strip()
    return "Services" if hsn.startswith("99") else "Goods"


def item_gst_rate_details(item, data: InvoiceData) -> list[tuple[str, float]]:
    """Return Tally item-invoice GST rate rows for one inventory line.

    TallyPrime exports manually corrected item vouchers with internal tax labels
    such as ``CGST`` and ``SGST/UTGST`` in ``ALLINVENTORYENTRIES.LIST``. For
    intra-state CGST/SGST rows it also stores the equivalent combined IGST rate.
    Mirroring that shape lets Tally show the item tax rate in voucher alteration.
    """
    components = tax_components_for_item(item)
    rates = {tax_type.upper(): values["rate"] for tax_type, values in components.items() if values["rate"] > 0}
    if not rates:
        rates = {
            duty_head_to_tax_type(duty_head): rate
            for duty_head, rate in gst_rate_details(data)
            if rate > 0
        }
    return tally_item_rate_rows(rates)


def tally_item_rate_rows(rates: dict[str, float]) -> list[tuple[str, float]]:
    """Return Tally's expected inventory ``RATEDETAILS.LIST`` rows."""
    cgst_rate = rates.get("CGST", 0.0)
    sgst_rate = rates.get("SGST", 0.0)
    igst_rate = rates.get("IGST", 0.0)
    cess_rate = rates.get("CESS", 0.0)
    combined_gst_rate = igst_rate or (cgst_rate + sgst_rate)

    rows: list[tuple[str, float]] = []
    if sgst_rate > 0:
        rows.append(("State Tax", sgst_rate))
    if cgst_rate > 0:
        rows.append(("CGST", cgst_rate))
    if sgst_rate > 0:
        rows.append(("SGST/UTGST", sgst_rate))
    if combined_gst_rate > 0:
        rows.append(("IGST", combined_gst_rate))
    rows.append(("Cess", cess_rate))
    rows.append(("State Cess", 0.0))
    return rows


def duty_head_to_tax_type(duty_head: str) -> str:
    """Map existing duty-head labels back to GST component codes."""
    normalized = duty_head.strip().upper()
    if normalized in {"CENTRAL TAX", "CGST"}:
        return "CGST"
    if normalized in {"STATE TAX", "SGST/UTGST", "SGST"}:
        return "SGST"
    if normalized in {"INTEGRATED TAX", "IGST"}:
        return "IGST"
    if normalized == "CESS":
        return "CESS"
    return normalized
