from __future__ import annotations

"""TallyPrime master preflight and XML builders."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from ...config import STATE_CODES
from ..settings import get_tally_settings
from .mapping import (
    INPUT_CESS_LEDGER as MAP_INPUT_CESS_LEDGER,
    INPUT_CGST_LEDGER as MAP_INPUT_CGST_LEDGER,
    INPUT_IGST_LEDGER as MAP_INPUT_IGST_LEDGER,
    INPUT_SGST_LEDGER as MAP_INPUT_SGST_LEDGER,
    PURCHASE_LEDGER as MAP_PURCHASE_LEDGER,
    STOCK_GROUP as MAP_STOCK_GROUP,
    STOCK_ITEM as MAP_STOCK_ITEM,
    UNIT as MAP_UNIT,
    VENDOR_GROUP as MAP_VENDOR_GROUP,
    VENDOR_LEDGER as MAP_VENDOR_LEDGER,
    mapped_default,
    mapped_value,
)
from .xml_utils import unique_collection_name
from ...domain.schemas import InvoiceData, LineItem
from ...domain.parsing import parse_date

VENDOR_LEDGER = "Vendor Ledger"
PURCHASE_LEDGER = "Purchase Ledger"
TAX_LEDGER = "Tax Ledger"
ROUND_OFF_LEDGER = "Round Off Ledger"
VOUCHER_TYPE = "Voucher Type"
UNIT_MASTER = "Unit Master"
STOCK_GROUP_MASTER = "Stock Group Master"
STOCK_ITEM_MASTER = "Stock Item Master"

PURCHASE_VOUCHER_TYPE = "Purchase"
TALLY_ANY = "\u0004 Any"
TALLY_NOT_APPLICABLE = "\u0004 Not Applicable"


@dataclass(frozen=True)
class TallyMaster:
    """One Tally master required before voucher posting."""

    name: str
    kind: str
    parent: str | None = None
    gstin: str | None = None
    address: str | None = None
    state: str | None = None
    country: str | None = None
    pan: str | None = None
    contact: str | None = None
    applicable_from: str | None = None
    tax_type: str | None = None
    unit_name: str | None = None
    stock_group: str | None = None
    hsn_sac: str | None = None
    gst_rates: tuple[tuple[str, float], ...] = ()
    supply_nature: str | None = None
    action: str = "Create"

    @property
    def label(self) -> str:
        """Return a display label for confirmation dialogs."""
        if self.parent:
            return f"{self.kind}: {self.name} under {self.parent}"
        return f"{self.kind}: {self.name}"


def required_purchase_masters(data: InvoiceData) -> list[TallyMaster]:
    """Return masters needed for ledger-only purchase posting."""
    masters = [
        vendor_master_from_invoice(data, action="Create"),
        TallyMaster(mapped_default(MAP_PURCHASE_LEDGER, get_tally_settings().purchase_ledger_name), PURCHASE_LEDGER, "Purchase Accounts"),
        *tax_ledger_masters(action="Create"),
        TallyMaster(PURCHASE_VOUCHER_TYPE, VOUCHER_TYPE, PURCHASE_VOUCHER_TYPE),
    ]
    if data.round_off:
        masters.append(TallyMaster("Round Off", ROUND_OFF_LEDGER, "Indirect Expenses"))
    return dedupe_masters(masters)


def required_inventory_purchase_masters(data: InvoiceData) -> list[TallyMaster]:
    """Return masters needed for inventory-based purchase posting."""
    stock_group = default_stock_group_name()
    masters = [*required_purchase_masters(data), stock_group_master(stock_group, action="Create")]
    stock_item_masters: list[TallyMaster] = []
    for item in data.line_items:
        unit_master = unit_master_from_line_item(item.unit, action="Create")
        if unit_master:
            masters.append(unit_master)
        stock_item_masters.append(
            stock_item_master_from_invoice_item(item, data, action="Create")
        )
    masters.extend(stock_item_masters)
    return dedupe_masters(masters)


def vendor_master_from_invoice(data: InvoiceData, *, action: str = "Alter") -> TallyMaster:
    """Build a vendor ledger master from extracted invoice data."""
    vendor_source = data.vendor_name or "Unknown Supplier"
    vendor_name = mapped_value(MAP_VENDOR_LEDGER, vendor_source, vendor_source)
    return TallyMaster(
        vendor_name,
        VENDOR_LEDGER,
        mapped_default(MAP_VENDOR_GROUP, get_tally_settings().tally_vendor_parent_ledger),
        data.vendor_gstin,
        address=data.vendor_address,
        state=vendor_state(data),
        country="India" if data.vendor_gstin or data.vendor_address else None,
        pan=data.vendor_pan or pan_from_gstin(data.vendor_gstin),
        contact=data.vendor_contact,
        applicable_from=fiscal_year_start(data.date),
        action=action,
    )


def unit_master_from_line_item(unit: str | None, *, action: str = "Create") -> TallyMaster | None:
    """Build a simple unit master from reviewed line-item text."""
    unit_name = mapped_unit_name(unit)
    if not unit_name:
        return None
    return TallyMaster(unit_name, UNIT_MASTER, action=action)


def stock_item_master_from_invoice_item(item: LineItem, data: InvoiceData, *, action: str = "Create") -> TallyMaster:
    """Build a stock item master from one reviewed invoice line item."""
    stock_name = stock_item_name_from_line_item(item)
    unit_name = mapped_unit_name(item.unit)
    return TallyMaster(
        stock_name,
        STOCK_ITEM_MASTER,
        parent=default_stock_group_name(),
        unit_name=unit_name,
        stock_group=default_stock_group_name(),
        hsn_sac=(item.hsn_sac or "").strip() or None,
        gst_rates=stock_item_gst_rates(item, data),
        supply_nature=stock_item_supply_nature(item),
        applicable_from=fiscal_year_start(data.date),
        action=action,
    )


def default_stock_group_name() -> str:
    """Return the runtime-configured default stock group for item-wise posting."""
    return mapped_default(MAP_STOCK_GROUP, get_tally_settings().default_stock_group or "Primary")

def stock_group_master(name: str | None = None, *, parent: str | None = None, action: str = "Create") -> TallyMaster:
    """Build a stock group master used by item-wise posting."""
    group_name = name or default_stock_group_name()
    return TallyMaster(group_name, STOCK_GROUP_MASTER, parent=parent, stock_group=group_name, action=action)


def build_master_import_xml(masters: Iterable[TallyMaster]) -> bytes:
    """Build one Tally import envelope for creating missing masters."""
    envelope, request_data = import_envelope("All Masters")
    for master in masters:
        message = SubElement(request_data, "TALLYMESSAGE")
        if master.kind == VOUCHER_TYPE:
            build_voucher_type(message, master)
        elif master.kind == UNIT_MASTER:
            build_unit(message, master)
        elif master.kind == STOCK_GROUP_MASTER:
            build_stock_group(message, master)
        elif master.kind == STOCK_ITEM_MASTER:
            build_stock_item(message, master)
        else:
            build_ledger(message, master)
    indent(envelope, space="  ")
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def build_vendor_master_xml(data: InvoiceData, *, action: str = "Alter") -> bytes:
    """Build XML that creates or enriches one vendor ledger master."""
    return build_master_import_xml([vendor_master_from_invoice(data, action=action)])


def build_system_ledgers_xml(*, action: str = "Alter") -> bytes:
    """Build XML that creates or enriches purchase and GST tax ledgers."""
    masters = [
        TallyMaster(mapped_default(MAP_PURCHASE_LEDGER, get_tally_settings().purchase_ledger_name), PURCHASE_LEDGER, "Purchase Accounts", action=action),
        *tax_ledger_masters(action=action),
    ]
    return build_master_import_xml(masters)


def build_inventory_stock_items_xml(data: InvoiceData, *, action: str = "Alter") -> bytes:
    """Build XML that enriches stock items with GST and HSN details."""
    masters = [stock_item_master_from_invoice_item(item, data, action=action) for item in data.line_items]
    return build_master_import_xml(dedupe_masters(masters))


def tax_ledger_masters(*, action: str = "Alter") -> list[TallyMaster]:
    """Return configured GST input tax ledger masters."""
    return [
        TallyMaster(mapped_default(MAP_INPUT_CGST_LEDGER, get_tally_settings().input_cgst_ledger_name), TAX_LEDGER, "Duties & Taxes", tax_type="CGST", action=action),
        TallyMaster(mapped_default(MAP_INPUT_SGST_LEDGER, get_tally_settings().input_sgst_ledger_name), TAX_LEDGER, "Duties & Taxes", tax_type="SGST", action=action),
        TallyMaster(mapped_default(MAP_INPUT_IGST_LEDGER, get_tally_settings().input_igst_ledger_name), TAX_LEDGER, "Duties & Taxes", tax_type="IGST", action=action),
        TallyMaster(mapped_default(MAP_INPUT_CESS_LEDGER, get_tally_settings().input_cess_ledger_name), TAX_LEDGER, "Duties & Taxes", tax_type="Cess", action=action),
    ]


def build_collection_export_xml(collection_name: str, master_type: str, company: str | None = None) -> bytes:
    """Build a Tally collection export request used for preflight existence checks."""
    unique_name = unique_collection_name(collection_name)
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    add_text(header, "VERSION", "1")
    add_text(header, "TALLYREQUEST", "Export")
    add_text(header, "TYPE", "Collection")
    add_text(header, "ID", unique_name)
    body = SubElement(envelope, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    add_text_if_company(static, company=company)
    add_text(static, "SVEXPORTFORMAT", "$$SysName:XML")
    tdl = SubElement(desc, "TDL")
    tdl_message = SubElement(tdl, "TDLMESSAGE")
    collection = SubElement(tdl_message, "COLLECTION", NAME=unique_name, ISMODIFY="No")
    add_text(collection, "TYPE", master_type)
    add_text(collection, "FETCH", "NAME")
    if master_type == "Unit":
        add_text(collection, "FETCH", "FORMALNAME")
    if master_type in {"Ledger", "Group"}:
        add_text(collection, "FETCH", "PARENT")
    indent(envelope, space="  ")
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def import_envelope(report_name: str) -> tuple[Element, Element]:
    """Create a standard Tally Import Data envelope and return REQUESTDATA."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    add_text(header, "TALLYREQUEST", "Import Data")
    body = SubElement(envelope, "BODY")
    import_data = SubElement(body, "IMPORTDATA")
    request_desc = SubElement(import_data, "REQUESTDESC")
    add_text(request_desc, "REPORTNAME", report_name)
    company = get_tally_settings().tally_company
    if company:
        static = SubElement(request_desc, "STATICVARIABLES")
        add_text(static, "SVCURRENTCOMPANY", company)
    request_data = SubElement(import_data, "REQUESTDATA")
    return envelope, request_data


def build_ledger(parent: Element, master: TallyMaster) -> Element:
    """Append a Ledger master XML node."""
    ledger = SubElement(parent, "LEDGER", NAME=master.name, ACTION=master.action)
    add_text(ledger, "NAME", master.name)
    add_text(ledger, "PARENT", master.parent or "Sundry Creditors")
    if master.kind == VENDOR_LEDGER:
        add_string_list(ledger, "MAILINGNAME.LIST", "MAILINGNAME", [master.name])
        add_string_list(ledger, "ADDRESS.LIST", "ADDRESS", address_lines(master.address))
        if master.state:
            add_text(ledger, "LEDSTATENAME", master.state)
            add_text(ledger, "STATENAME", master.state)
        if master.country:
            add_text(ledger, "COUNTRYNAME", master.country)
            add_text(ledger, "COUNTRYOFRESIDENCE", master.country)
        pincode = pincode_from_address(master.address)
        if pincode:
            add_text(ledger, "PINCODE", pincode)
        if master.pan:
            add_text(ledger, "INCOMETAXNUMBER", master.pan)
        if master.contact:
            add_text(ledger, "LEDGERCONTACT", master.contact)
            add_text(ledger, "LEDGERPHONE", master.contact)
        add_vendor_mailing_details(ledger, master)
        add_vendor_gst_details(ledger, master)
    elif master.kind == TAX_LEDGER:
        add_tax_ledger_details(ledger, master)
    add_text(ledger, "ISBILLWISEON", "Yes" if master.kind == VENDOR_LEDGER else "No")
    if master.gstin:
        add_text(ledger, "GSTREGISTRATIONTYPE", "Regular")
        add_text(ledger, "PARTYGSTIN", master.gstin)
        add_text(ledger, "GSTIN", master.gstin)
    return ledger


def build_unit(parent: Element, master: TallyMaster) -> Element:
    """Append a Unit master XML node."""
    unit = SubElement(parent, "UNIT", NAME=master.name, RESERVEDNAME="", ACTION=master.action)
    add_text(unit, "NAME", master.name)
    add_text(unit, "GSTREPUOM", gst_reporting_uqc(master.name))
    add_text(unit, "ISGSTEXCLUDED", "No")
    add_text(unit, "ISSIMPLEUNIT", "Yes")
    add_text(unit, "DECIMALPLACES", "2")
    reporting = SubElement(unit, "REPORTINGUQCDETAILS.LIST")
    add_text(reporting, "APPLICABLEFROM", current_fiscal_year_start())
    add_text(reporting, "REPORTINGUQCNAME", gst_reporting_uqc(master.name))
    return unit


def build_stock_item(parent: Element, master: TallyMaster) -> Element:
    """Append a Stock Item master XML node."""
    item = SubElement(parent, "STOCKITEM", NAME=master.name, ACTION=master.action)
    add_text(item, "NAME", master.name)
    add_text(item, "PARENT", master.stock_group or master.parent or default_stock_group_name())
    if master.unit_name:
        add_text(item, "BASEUNITS", master.unit_name)
        add_text(item, "VATBASEUNIT", master.unit_name)
    add_text(item, "GSTAPPLICABLE", "Applicable")
    add_text(item, "GSTTYPEOFSUPPLY", master.supply_nature or "Goods")
    if master.hsn_sac:
        add_text(item, "HSNCODE", master.hsn_sac)
        add_text(item, "GSTHSNNAME", master.hsn_sac)
    add_stock_item_gst_details(item, master)
    add_stock_item_hsn_details(item, master)
    return item


def build_stock_group(parent: Element, master: TallyMaster) -> Element:
    """Append a Stock Group master XML node."""
    group = SubElement(parent, "STOCKGROUP", NAME=master.name, ACTION=master.action)
    add_text(group, "NAME", master.name)
    if master.parent:
        add_text(group, "PARENT", master.parent)
    return group


def add_tax_ledger_details(ledger: Element, master: TallyMaster) -> None:
    """Append GST duty metadata to an input tax ledger."""
    tax_type = master.tax_type or tax_type_from_name(master.name)
    add_text(ledger, "TAXTYPE", "GST")
    add_text(ledger, "GSTTYPE", tally_tax_type_label(tax_type))
    add_text(ledger, "GSTDUTYHEAD", tally_tax_type_label(tax_type))
    add_text(ledger, "SUBTAXTYPE", tally_tax_type_label(tax_type))
    add_text(ledger, "BASICTYPEOFDUTY", "GST")
    add_text(ledger, "APPROPRIATEFOR", "Input Tax Credit")
    add_text(ledger, "GSTAPPROPRIATETO", "Goods and Services Tax")
    add_text(ledger, "DUTYHEAD", duty_head_from_tax_type(tax_type))
    add_text(ledger, "RATEOFTAXCALCULATION", "0")
    add_text(ledger, "ISGSTAPPLICABLE", "Yes")
    add_text(ledger, "ISINPUTCREDIT", "Yes")
    add_text(ledger, "APPROPRIATETAXVALUE", "Yes")
    add_text(ledger, "AFFECTSSTOCK", "No")


def add_vendor_mailing_details(ledger: Element, master: TallyMaster) -> None:
    """Append TallyPrime's effective-dated mailing details list."""
    details = SubElement(ledger, "LEDMAILINGDETAILS.LIST")
    add_text(details, "APPLICABLEFROM", master.applicable_from or today_tally_date())
    add_string_list(details, "MAILINGNAME.LIST", "MAILINGNAME", [master.name])
    add_string_list(details, "ADDRESS.LIST", "ADDRESS", address_lines(master.address))
    if master.state:
        add_text(details, "STATE", master.state)
        add_text(details, "STATENAME", master.state)
    if master.country:
        add_text(details, "COUNTRY", master.country)
        add_text(details, "COUNTRYNAME", master.country)
    pincode = pincode_from_address(master.address)
    if pincode:
        add_text(details, "PINCODE", pincode)


def add_vendor_gst_details(ledger: Element, master: TallyMaster) -> None:
    """Append TallyPrime's effective-dated GST registration details list."""
    if not master.gstin:
        return
    details = SubElement(ledger, "LEDGSTREGDETAILS.LIST")
    add_text(details, "APPLICABLEFROM", master.applicable_from or today_tally_date())
    add_text(details, "GSTREGISTRATIONTYPE", "Regular")
    if master.state:
        add_text(details, "PLACEOFSUPPLY", master.state)
        add_text(details, "STATE", master.state)
    add_text(details, "GSTIN", master.gstin)
    add_text(details, "PARTYGSTIN", master.gstin)


def add_stock_item_gst_details(item: Element, master: TallyMaster) -> None:
    """Append GST/HSN details to a stock item master."""
    details = SubElement(item, "GSTDETAILS.LIST")
    add_text(details, "APPLICABLEFROM", master.applicable_from or today_tally_date())
    add_text(details, "TAXABILITY", "Taxable")
    add_text(details, "SRCOFGSTDETAILS", "Specify Details Here")
    add_text(details, "GSTCALCSLABONMRP", "No")
    add_text(details, "ISREVERSECHARGEAPPLICABLE", "No")
    add_text(details, "ISNONGSTGOODS", "No")
    add_text(details, "GSTINELIGIBLEITC", "No")
    add_text(details, "INCLUDEEXPFORSLABCALC", "No")
    statewise = SubElement(details, "STATEWISEDETAILS.LIST")
    add_text(statewise, "STATENAME", TALLY_ANY)
    for duty_head, rate in master.gst_rates:
        rate_details = SubElement(statewise, "RATEDETAILS.LIST")
        add_text(rate_details, "GSTRATEDUTYHEAD", duty_head)
        add_text(rate_details, "GSTRATEVALUATIONTYPE", "Based on Value" if rate > 0 else TALLY_NOT_APPLICABLE)
        add_text(rate_details, "GSTRATE", f"{rate:g}" if rate > 0 else "0")
        add_text(rate_details, "GSTRATEPERUNIT", "0")
    SubElement(details, "TEMPGSTITEMSLABRATES.LIST")
    SubElement(details, "TEMPGSTDETAILSLABRATES.LIST")


def add_stock_item_hsn_details(item: Element, master: TallyMaster) -> None:
    """Append HSN/SAC details to a stock item master."""
    if not master.hsn_sac:
        return
    details = SubElement(item, "HSNDETAILS.LIST")
    add_text(details, "APPLICABLEFROM", master.applicable_from or today_tally_date())
    add_text(details, "SRCOFHSNDETAILS", "Specify Details Here")
    add_text(details, "HSNCODE", master.hsn_sac)
    add_text(details, "DESCRIPTION", master.name)


def build_voucher_type(parent: Element, master: TallyMaster) -> Element:
    """Append a Voucher Type master XML node."""
    voucher_type = SubElement(parent, "VOUCHERTYPE", NAME=master.name, ACTION="Create")
    add_text(voucher_type, "NAME", master.name)
    add_text(voucher_type, "PARENT", master.parent or PURCHASE_VOUCHER_TYPE)
    add_text(voucher_type, "NUMBERINGMETHOD", "Automatic")
    return voucher_type


def add_text_if_company(parent: Element, company: str | None = None) -> None:
    """Add the company static variable when configured."""
    selected_company = company if company is not None else get_tally_settings().tally_company
    if selected_company:
        add_text(parent, "SVCURRENTCOMPANY", selected_company)


def add_text(parent: Element, tag: str, text: object) -> Element:
    """Append a text child node."""
    node = SubElement(parent, tag)
    node.text = str(text)
    return node


def add_string_list(parent: Element, list_tag: str, value_tag: str, values: Iterable[str]) -> None:
    """Append a Tally string list when at least one value is present."""
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return
    list_node = SubElement(parent, list_tag, TYPE="String")
    for value in cleaned:
        add_text(list_node, value_tag, value)


def address_lines(address: str | None) -> list[str]:
    """Split extracted address into Tally-friendly lines."""
    if not address:
        return []
    parts = [part.strip() for part in re.split(r"[\r\n,]+", address) if part.strip()]
    if len(parts) > 1:
        return parts[:4]
    return [address.strip()]


def vendor_state(data: InvoiceData) -> str | None:
    """Return the vendor state name from explicit state code or GSTIN."""
    code = data.vendor_state_code
    if not code and data.vendor_gstin and len(data.vendor_gstin) >= 2:
        code = data.vendor_gstin[:2]
    return STATE_CODES.get(code or "")


def pan_from_gstin(gstin: str | None) -> str | None:
    """Derive PAN from a 15-character GSTIN."""
    if gstin and len(gstin.strip()) == 15:
        return gstin.strip()[2:12]
    return None


def pincode_from_address(address: str | None) -> str | None:
    """Extract a trailing Indian PIN code from an address when visible."""
    if not address:
        return None
    match = re.search(r"\b(\d{6})\b", address)
    return match.group(1) if match else None


def fiscal_year_start(value: str | None) -> str:
    """Return the Indian financial year start date for a Tally date value."""
    dt = parse_date(value) or datetime.now()
    year = dt.year if dt.month >= 4 else dt.year - 1
    return datetime(year, 4, 1).strftime("%Y%m%d")


def today_tally_date() -> str:
    """Return today's date in Tally import format."""
    return datetime.now().strftime("%Y%m%d")


def tax_type_from_name(name: str) -> str:
    """Infer GST tax type from a configured ledger name."""
    lower = name.lower()
    if "cgst" in lower:
        return "CGST"
    if "sgst" in lower or "utgst" in lower:
        return "SGST"
    if "igst" in lower:
        return "IGST"
    if "cess" in lower:
        return "Cess"
    return "GST"


def duty_head_from_tax_type(tax_type: str) -> str:
    """Map tax type to Tally duty head label."""
    normalized = tax_type.upper()
    if normalized == "CGST":
        return "Central Tax"
    if normalized == "SGST":
        return "State Tax"
    if normalized == "IGST":
        return "Integrated Tax"
    if normalized == "CESS":
        return "Cess"
    return tax_type


def tally_tax_type_label(tax_type: str) -> str:
    """Return the label TallyPrime expects in tax-ledger Tax type."""
    normalized = tax_type.upper()
    if normalized == "CGST":
        return "CGST"
    if normalized == "SGST":
        return "SGST/UTGST"
    if normalized == "IGST":
        return "IGST"
    if normalized == "CESS":
        return "Cess"
    return tax_type


def stock_item_gst_rates(item, data: InvoiceData) -> tuple[tuple[str, float], ...]:
    """Return GST rate rows for one stock item, falling back to invoice-level rates."""
    rates: dict[str, float] = {}
    for tax in item.taxes:
        tax_type = tax.tax_type.upper()
        if tax_type in {"CGST", "SGST", "IGST", "CESS"} and tax.tax_rate > 0:
            rates[tax_type] = max(rates.get(tax_type, 0.0), tax.tax_rate)
    if not rates and data.total_taxable_amount > 0:
        totals = {
            "CGST": data.total_cgst,
            "SGST": data.total_sgst,
            "IGST": data.total_igst,
            "CESS": data.total_cess,
        }
        for tax_type, amount in totals.items():
            if amount > 0:
                rates[tax_type] = round((amount / data.total_taxable_amount) * 100, 2)
    ordered: list[tuple[str, float]] = []
    cgst_rate = rates.get("CGST", 0.0)
    sgst_rate = rates.get("SGST", 0.0)
    igst_rate = rates.get("IGST", 0.0) or (cgst_rate + sgst_rate)
    if igst_rate > 0:
        ordered.append((tally_tax_type_label("IGST"), igst_rate))
    for tax_type in ("CGST", "SGST", "CESS"):
        if tax_type in rates:
            ordered.append((tally_tax_type_label(tax_type), rates[tax_type]))
    ordered.append(("State Cess", 0.0))
    return tuple(ordered)


def stock_item_supply_nature(item) -> str:
    """Infer whether one reviewed stock item is goods or services."""
    hsn = (item.hsn_sac or "").strip()
    return "Services" if hsn.startswith("99") else "Goods"


def dedupe_masters(masters: Iterable[TallyMaster]) -> list[TallyMaster]:
    """De-duplicate masters by type/name while preserving order."""
    seen: set[tuple[str, str]] = set()
    unique: list[TallyMaster] = []
    for master in masters:
        key = (master.kind.lower(), master.name.strip().lower())
        if key not in seen:
            seen.add(key)
            unique.append(master)
    return unique


def normalize_unit_name(unit: str | None) -> str | None:
    """Normalize a reviewed unit label for Tally master names."""
    if not unit:
        return None
    cleaned = " ".join(unit.strip().upper().split())
    return cleaned or None


def mapped_unit_name(unit: str | None) -> str | None:
    """Return the confirmed Tally unit value, falling back to normalized invoice text."""
    normalized = normalize_unit_name(unit)
    if not normalized:
        return None
    return mapped_value(MAP_UNIT, normalized, normalized)


def gst_reporting_uqc(unit_name: str) -> str:
    """Return a GST/UQC label for a Tally stock unit."""
    normalized = normalize_unit_name(unit_name) or "NOS"
    mapping = {
        "KGS": "KGS-KILOGRAMS",
        "KG": "KGS-KILOGRAMS",
        "KILOGRAM": "KGS-KILOGRAMS",
        "KILOGRAMS": "KGS-KILOGRAMS",
        "LTR": "LTR-LITRES",
        "LITRE": "LTR-LITRES",
        "LITRES": "LTR-LITRES",
        "LITER": "LTR-LITRES",
        "LITERS": "LTR-LITRES",
        "MTR": "MTR-METERS",
        "METER": "MTR-METERS",
        "METERS": "MTR-METERS",
        "METRE": "MTR-METERS",
        "METRES": "MTR-METERS",
        "NOS": "NOS-NUMBERS",
        "NO": "NOS-NUMBERS",
        "NUMBER": "NOS-NUMBERS",
        "NUMBERS": "NOS-NUMBERS",
        "PCS": "PCS-PIECES",
        "PC": "PCS-PIECES",
        "PIECE": "PCS-PIECES",
        "PIECES": "PCS-PIECES",
        "PRS": "PRS-PAIRS",
        "PAIR": "PRS-PAIRS",
        "PAIRS": "PRS-PAIRS",
        "DOZ": "DOZ-DOZENS",
        "DOZEN": "DOZ-DOZENS",
        "DOZENS": "DOZ-DOZENS",
    }
    return mapping.get(normalized, "OTH-OTHERS")


def current_fiscal_year_start() -> str:
    """Return the current Indian financial year start in Tally date format."""
    now = datetime.now()
    year = now.year if now.month >= 4 else now.year - 1
    return datetime(year, 4, 1).strftime("%Y%m%d")


def normalize_stock_item_name(description: str | None) -> str:
    """Normalize a reviewed line description for stock item master names."""
    cleaned = " ".join((description or "").strip().split())
    return cleaned or "Unknown Item"


def stock_item_name_from_line_item(item: LineItem) -> str:
    """Return the reviewed Tally stock item name, preferring clean item_name."""
    source = normalize_stock_item_name(item.item_name or item.description)
    return mapped_value(MAP_STOCK_ITEM, source, source)
