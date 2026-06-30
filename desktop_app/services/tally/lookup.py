from __future__ import annotations

"""Lookup helpers for TallyPrime vouchers created by direct posting."""

from dataclasses import dataclass
from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement, tostring

from .xml_utils import sanitize_xml_text, unique_collection_name


@dataclass(frozen=True)
class TallyVoucherDetails:
    """Selected fields read back from a posted Tally voucher."""

    voucher_number: str | None = None
    voucher_type: str | None = None
    date: str | None = None
    party_invoice_number: str | None = None
    reference: str | None = None
    master_id: str | None = None


def build_posted_voucher_lookup_xml(last_voucher_id: str, company: str | None = None, *, id_field: str = "MASTERID") -> bytes:
    """Build a Tally collection export filtered by one posted voucher ID field."""
    collection_name = unique_collection_name("BahiAIPostedVoucher")
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "VERSION").text = "1"
    SubElement(header, "TALLYREQUEST").text = "Export"
    SubElement(header, "TYPE").text = "Collection"
    SubElement(header, "ID").text = collection_name

    body = SubElement(envelope, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    SubElement(static, "SVEXPORTFORMAT").text = "$$SysName:XML"
    if company:
        SubElement(static, "SVCURRENTCOMPANY").text = company

    tdl = SubElement(desc, "TDL")
    message = SubElement(tdl, "TDLMESSAGE")
    collection = SubElement(message, "COLLECTION", NAME=collection_name, ISMODIFY="No")
    SubElement(collection, "TYPE").text = "Voucher"
    SubElement(collection, "FETCH").text = "VOUCHERNUMBER,VOUCHERTYPENAME,DATE,PARTYINVNO,REFERENCE,MASTERID,VOUCHERID"
    SubElement(collection, "FILTERS").text = "BahiAIVoucherByMasterId"
    formula = SubElement(message, "SYSTEM", TYPE="Formulae", NAME="BahiAIVoucherByMasterId")
    formula.text = f"${id_field.upper()} = {last_voucher_id}"
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def parse_posted_voucher_details(xml_text: str) -> TallyVoucherDetails | None:
    """Parse a Tally voucher collection response into selected voucher fields."""
    try:
        root = ElementTree.fromstring(sanitize_xml_text(xml_text).strip())
    except (ElementTree.ParseError, ValueError):
        return None

    voucher = first_voucher_object(root)
    source = voucher if voucher is not None else root

    details = TallyVoucherDetails(
        voucher_number=first_text(source, "VOUCHERNUMBER"),
        voucher_type=first_text(source, "VOUCHERTYPENAME"),
        date=first_text(source, "DATE"),
        party_invoice_number=first_text(source, "PARTYINVNO"),
        reference=first_text(source, "REFERENCE"),
        master_id=first_text(source, "MASTERID") or first_text(source, "VOUCHERID"),
    )
    return details if any(details.__dict__.values()) else None


def first_voucher_object(root: ElementTree.Element) -> ElementTree.Element | None:
    """Return the first actual voucher object, ignoring CMPINFO count fields."""
    for element in root.iter():
        if element.tag.upper() == "VOUCHER" and (element.attrib or first_text(element, "VOUCHERNUMBER")):
            return element
    return None


def first_text(root: ElementTree.Element, tag: str) -> str | None:
    """Read the first non-empty text for a tag below root."""
    wanted = tag.upper()
    for element in root.iter():
        if element.tag.upper() == wanted and element.text and element.text.strip():
            return element.text.strip()
    return None
