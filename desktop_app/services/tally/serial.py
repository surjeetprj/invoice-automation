from __future__ import annotations

"""TallyPrime serial verification XML and parsing helpers."""

from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement, tostring


def mask_serial(value: str | None) -> str:
    """Return a log-safe Tally serial representation."""
    serial = str(value or "").strip()
    if len(serial) <= 4:
        return "****" if serial else ""
    return f"***{serial[-4:]}"


def tally_response_summary(value: str | None, *, limit: int = 220) -> str:
    """Return a compact one-line Tally response snippet for probe diagnostics."""
    text = " ".join(str(value or "").split())
    if not text:
        return "<empty>"
    return text if len(text) <= limit else text[:limit] + "..."


def build_tally_about_page_xml() -> bytes:
    """Build a Tally Product AboutPage export request for product information."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "VERSION").text = "1"
    SubElement(header, "TALLYREQUEST").text = "Export"
    SubElement(header, "TYPE").text = "Data"
    SubElement(header, "ID").text = "Product AboutPage"
    body = SubElement(envelope, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    SubElement(static, "SVEXPORTFORMAT").text = "$$SysName:XML"
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def parse_tally_about_page_serial_number(xml_text: str) -> str | None:
    """Extract the serial number from TallyPrime Product AboutPage XML."""
    try:
        root = ElementTree.fromstring(str(xml_text or "").strip())
    except ElementTree.ParseError:
        return None
    waiting_for_serial_value = False
    for element in root.iter():
        tag = element.tag.upper().replace(".", "").replace("_", "")
        text = " ".join(str(element.text or "").split())
        if tag == "ABOUTPAGEPROMPT" and text.lower() == "serial number":
            waiting_for_serial_value = True
            continue
        if waiting_for_serial_value and tag == "ABOUTPAGEINFO":
            return serial_value(text)
    return None


def serial_value(value: str | None) -> str | None:
    """Normalize and validate a candidate Tally serial value."""
    text = " ".join(str(value or "").strip().split())
    return text if len(text) >= 4 else None
