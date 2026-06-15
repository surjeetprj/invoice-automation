from __future__ import annotations

"""TallyPrime master preflight and XML builders."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from xml.etree.ElementTree import Element, SubElement, indent, tostring

from ...config import (
    INPUT_CESS_LEDGER_NAME,
    INPUT_CGST_LEDGER_NAME,
    INPUT_IGST_LEDGER_NAME,
    INPUT_SGST_LEDGER_NAME,
    PURCHASE_LEDGER_NAME,
    STATE_CODES,
    TALLY_COMPANY,
    TALLY_VENDOR_PARENT_LEDGER,
)
from ...domain.schemas import InvoiceData
from ...domain.parsing import parse_date


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
    action: str = "Create"

    @property
    def label(self) -> str:
        """Return a display label for confirmation dialogs."""
        if self.parent:
            return f"{self.kind}: {self.name} under {self.parent}"
        return f"{self.kind}: {self.name}"


def required_purchase_masters(data: InvoiceData) -> list[TallyMaster]:
    """Return masters needed for ledger-only purchase posting."""
    vendor_name = data.vendor_name or "Unknown Supplier"
    masters = [
        vendor_master_from_invoice(data, action="Create"),
        TallyMaster(PURCHASE_LEDGER_NAME, "Purchase Ledger", "Purchase Accounts"),
        *tax_ledger_masters(action="Create"),
        TallyMaster("Purchase", "Voucher Type", "Purchase"),
    ]
    if data.round_off:
        masters.append(TallyMaster("Round Off", "Round Off Ledger", "Indirect Expenses"))
    return dedupe_masters(masters)


def vendor_master_from_invoice(data: InvoiceData, *, action: str = "Alter") -> TallyMaster:
    """Build a vendor ledger master from extracted invoice data."""
    vendor_name = data.vendor_name or "Unknown Supplier"
    return TallyMaster(
        vendor_name,
        "Vendor Ledger",
        TALLY_VENDOR_PARENT_LEDGER,
        data.vendor_gstin,
        address=data.vendor_address,
        state=vendor_state(data),
        country="India" if data.vendor_gstin or data.vendor_address else None,
        pan=data.vendor_pan or pan_from_gstin(data.vendor_gstin),
        contact=data.vendor_contact,
        applicable_from=fiscal_year_start(data.date),
        action=action,
    )


def build_master_import_xml(masters: Iterable[TallyMaster]) -> bytes:
    """Build one Tally import envelope for creating missing masters."""
    envelope, request_data = import_envelope("All Masters")
    for master in masters:
        message = SubElement(request_data, "TALLYMESSAGE")
        if master.kind == "Voucher Type":
            build_voucher_type(message, master)
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
        TallyMaster(PURCHASE_LEDGER_NAME, "Purchase Ledger", "Purchase Accounts", action=action),
        *tax_ledger_masters(action=action),
    ]
    return build_master_import_xml(masters)


def tax_ledger_masters(*, action: str = "Alter") -> list[TallyMaster]:
    """Return configured GST input tax ledger masters."""
    return [
        TallyMaster(INPUT_CGST_LEDGER_NAME, "Tax Ledger", "Duties & Taxes", tax_type="CGST", action=action),
        TallyMaster(INPUT_SGST_LEDGER_NAME, "Tax Ledger", "Duties & Taxes", tax_type="SGST", action=action),
        TallyMaster(INPUT_IGST_LEDGER_NAME, "Tax Ledger", "Duties & Taxes", tax_type="IGST", action=action),
        TallyMaster(INPUT_CESS_LEDGER_NAME, "Tax Ledger", "Duties & Taxes", tax_type="Cess", action=action),
    ]


def build_collection_export_xml(collection_name: str, master_type: str) -> bytes:
    """Build a Tally collection export request used for preflight existence checks."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    add_text(header, "VERSION", "1")
    add_text(header, "TALLYREQUEST", "Export")
    add_text(header, "TYPE", "Collection")
    add_text(header, "ID", collection_name)
    body = SubElement(envelope, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    add_text_if_company(static)
    add_text(static, "SVEXPORTFORMAT", "$$SysName:XML")
    tdl = SubElement(desc, "TDL")
    tdl_message = SubElement(tdl, "TDLMESSAGE")
    collection = SubElement(tdl_message, "COLLECTION", NAME=collection_name, ISMODIFY="No")
    add_text(collection, "TYPE", master_type)
    add_text(collection, "FETCH", "NAME")
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
    if TALLY_COMPANY:
        static = SubElement(request_desc, "STATICVARIABLES")
        add_text(static, "SVCURRENTCOMPANY", TALLY_COMPANY)
    request_data = SubElement(import_data, "REQUESTDATA")
    return envelope, request_data


def build_ledger(parent: Element, master: TallyMaster) -> Element:
    """Append a Ledger master XML node."""
    ledger = SubElement(parent, "LEDGER", NAME=master.name, ACTION=master.action)
    add_text(ledger, "NAME", master.name)
    add_text(ledger, "PARENT", master.parent or "Sundry Creditors")
    if master.kind == "Vendor Ledger":
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
    elif master.kind == "Tax Ledger":
        add_tax_ledger_details(ledger, master)
    add_text(ledger, "ISBILLWISEON", "Yes" if master.kind == "Vendor Ledger" else "No")
    if master.gstin:
        add_text(ledger, "GSTREGISTRATIONTYPE", "Regular")
        add_text(ledger, "PARTYGSTIN", master.gstin)
        add_text(ledger, "GSTIN", master.gstin)
    return ledger


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


def build_voucher_type(parent: Element, master: TallyMaster) -> Element:
    """Append a Voucher Type master XML node."""
    voucher_type = SubElement(parent, "VOUCHERTYPE", NAME=master.name, ACTION="Create")
    add_text(voucher_type, "NAME", master.name)
    add_text(voucher_type, "PARENT", master.parent or "Purchase")
    add_text(voucher_type, "NUMBERINGMETHOD", "Automatic")
    return voucher_type


def add_text_if_company(parent: Element) -> None:
    """Add the company static variable when configured."""
    if TALLY_COMPANY:
        add_text(parent, "SVCURRENTCOMPANY", TALLY_COMPANY)


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
