from __future__ import annotations

"""HTTP/XML client for local TallyPrime direct posting."""

from dataclasses import dataclass
import logging
from typing import Iterable
from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement, tostring

import requests

from ..settings import get_tally_settings
from ...domain.schemas import InvoiceData
from .masters import (
    STOCK_GROUP_MASTER,
    STOCK_ITEM_MASTER,
    TallyMaster,
    UNIT_MASTER,
    VOUCHER_TYPE,
    build_collection_export_xml,
    build_inventory_stock_items_xml,
    build_master_import_xml,
    build_system_ledgers_xml,
    build_vendor_master_xml,
    required_inventory_purchase_masters,
    required_purchase_masters,
)
from .responses import TallyResponse, parse_tally_response
from .vouchers import build_inventory_purchase_voucher_xml, build_purchase_voucher_xml, gst_amount_details

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TallyPreflight:
    """Required and missing Tally masters for one invoice."""

    required_masters: tuple[TallyMaster, ...]
    missing_masters: tuple[TallyMaster, ...]

    @property
    def has_missing(self) -> bool:
        """Return True when master creation is needed before posting."""
        return bool(self.missing_masters)

    def missing_labels(self) -> list[str]:
        """Return display labels for missing masters."""
        return [master.label for master in self.missing_masters]


@dataclass(frozen=True)
class TallyPostResult:
    """Result returned by direct Tally posting workflow."""

    success: bool
    message: str
    response: TallyResponse | None = None


class TallyClient:
    """Small TallyPrime HTTP/XML client."""

    def __init__(self, url: str | None = None, timeout: int | None = None) -> None:
        settings = get_tally_settings()
        self.url = url or settings.tally_url
        self.timeout = timeout or settings.tally_timeout_seconds

    def post_xml(self, xml: bytes) -> str:
        """POST native Tally XML to the local TallyPrime HTTP server."""
        try:
            response = requests.post(
                self.url,
                data=xml,
                headers={"Content-Type": "application/xml", "Accept": "application/xml"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            raise ConnectionError(f"Could not connect to TallyPrime at {self.url}: {exc}") from exc

    def fetch_tally_serial_number(self) -> str:
        """Return the connected TallyPrime serial from Product AboutPage."""
        logger.info("Tally serial verification started")
        logger.info("Tally serial probe started: Product AboutPage")
        raw = self.post_xml(build_tally_about_page_xml())
        serial = parse_tally_about_page_serial_number(raw)
        if serial:
            logger.info("Tally serial verified using Product AboutPage probe: %s", mask_serial(serial))
            return serial

        logger.error("Tally serial probe did not return a serial: Product AboutPage | response=%s", tally_response_summary(raw))
        raise ConnectionError(
            "Could not verify TallyPrime serial number. Product AboutPage did not expose the "
            "TallyPrime serial number. TallyPrime export is blocked for this license."
        )

    def check_connection(self) -> None:
        """Raise if the TallyPrime HTTP endpoint cannot be reached."""
        self.fetch_master_names("InvoiceAIConnectionCheck", "Company")

    def fetch_company_names(self) -> set[str]:
        """Fetch available company names from TallyPrime."""
        raw = self.post_xml(build_tally_companies_xml())
        return parse_master_names(raw)

    def fetch_master_names(self, collection_name: str, master_type: str, company: str | None = None) -> set[str]:
        """Fetch master names for a Tally collection type."""
        xml = build_collection_export_xml(collection_name, master_type, company=company)
        raw = self.post_xml(xml)
        return parse_master_names(raw)

    def preflight_purchase_invoice(self, data: InvoiceData) -> TallyPreflight:
        """Check which required purchase masters are missing in TallyPrime."""
        required = tuple(required_purchase_masters(data))
        return self._preflight_masters(required)

    def preflight_inventory_purchase_invoice(self, data: InvoiceData) -> TallyPreflight:
        """Check line-item readiness and required stock masters for inventory posting."""
        validate_inventory_item_posting(data)
        required = tuple(required_inventory_purchase_masters(data))
        return self._preflight_masters(required)

    def create_missing_masters(self, masters: Iterable[TallyMaster]) -> TallyResponse:
        """Create missing masters and return Tally's normalized response."""
        xml = build_master_import_xml(masters)
        return parse_tally_response(self.post_xml(xml))

    def create_missing_inventory_masters(self, masters: Iterable[TallyMaster]) -> TallyResponse:
        """Create missing inventory masters one by one so failures identify the exact master."""
        responses: list[TallyResponse] = []
        for master in prioritize_inventory_masters(masters):
            response = self.create_missing_masters((master,))
            responses.append(annotate_tally_response(response, master.label))
            if not response.success:
                return merge_tally_responses(responses)
        return merge_tally_responses(responses) if responses else TallyResponse(success=True)

    def sync_vendor_master(self, data: InvoiceData) -> TallyResponse:
        """Alter the vendor ledger with extracted mailing and tax details."""
        xml = build_vendor_master_xml(data, action="Alter")
        return parse_tally_response(self.post_xml(xml))

    def sync_system_ledgers(self) -> TallyResponse:
        """Alter purchase and GST tax ledgers with expected accounting metadata."""
        xml = build_system_ledgers_xml(action="Alter")
        return parse_tally_response(self.post_xml(xml))

    def sync_inventory_item_masters(self, data: InvoiceData) -> TallyResponse:
        """Alter stock item masters with HSN and GST rate details."""
        xml = build_inventory_stock_items_xml(data, action="Alter")
        return parse_tally_response(self.post_xml(xml))

    def post_purchase_voucher(self, invoice_id: int, data: InvoiceData) -> TallyResponse:
        """Post a ledger-only Purchase voucher to TallyPrime."""
        xml = build_purchase_voucher_xml(invoice_id, data)
        return parse_tally_response(self.post_xml(xml))

    def post_inventory_purchase_voucher(self, invoice_id: int, data: InvoiceData) -> TallyResponse:
        """Post an item-wise/inventory Purchase voucher to TallyPrime."""
        validate_inventory_item_posting(data)
        xml = build_inventory_purchase_voucher_xml(invoice_id, data)
        return parse_tally_response(self.post_xml(xml))

    def _preflight_masters(self, required: tuple[TallyMaster, ...]) -> TallyPreflight:
        """Check which of the requested masters do not exist in TallyPrime."""
        ledger_names = normalized_names(self.fetch_master_names("InvoiceAILedgers", "Ledger"))
        voucher_type_names = normalized_names(self.fetch_master_names("InvoiceAIVoucherTypes", VOUCHER_TYPE))
        unit_names = normalized_names(self.fetch_master_names("InvoiceAIUnits", "Unit"))
        stock_group_names = normalized_names(self.fetch_master_names("InvoiceAIStockGroups", "Stock Group"))
        stock_item_names = normalized_names(self.fetch_master_names("InvoiceAIStockItems", "Stock Item"))
        names_by_kind = {
            VOUCHER_TYPE: voucher_type_names,
            UNIT_MASTER: unit_names,
            STOCK_GROUP_MASTER: stock_group_names,
            STOCK_ITEM_MASTER: stock_item_names,
        }
        missing: list[TallyMaster] = []
        for master in required:
            names = names_by_kind.get(master.kind, ledger_names)
            if normalize_name(master.name) not in names:
                missing.append(master)
        return TallyPreflight(required_masters=required, missing_masters=tuple(missing))


def parse_master_names(xml_text: str) -> set[str]:
    """Parse master names returned by a Tally collection export."""
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError:
        return set()
    names: set[str] = set()
    for element in root.iter():
        tag = element.tag.upper()
        attr_name = element.attrib.get("NAME") or element.attrib.get("Name")
        if attr_name:
            names.add(attr_name.strip())
        if tag in {"NAME", "LEDGERNAME", "VOUCHERTYPENAME"} and element.text and element.text.strip():
            names.add(element.text.strip())
    return names


def normalize_name(value: str) -> str:
    """Normalize a Tally master name for case-insensitive matching."""
    return " ".join(value.strip().lower().split())


def normalized_names(values: set[str]) -> set[str]:
    """Normalize a set of Tally master names for comparisons."""
    return {normalize_name(value) for value in values}


def prioritize_inventory_masters(masters: Iterable[TallyMaster]) -> tuple[TallyMaster, ...]:
    """Return inventory masters with stock items after their dependencies."""
    ordered = tuple(masters)
    return tuple(master for master in ordered if master.kind != STOCK_ITEM_MASTER) + tuple(
        master for master in ordered if master.kind == STOCK_ITEM_MASTER
    )


def merge_tally_responses(responses: Iterable[TallyResponse]) -> TallyResponse:
    """Combine multiple Tally responses into one aggregate result."""
    collected = tuple(responses)
    if not collected:
        return TallyResponse(success=True)
    messages: list[str] = []
    last_voucher_id: str | None = None
    raw_xml_parts: list[str] = []
    for response in collected:
        if response.last_voucher_id:
            last_voucher_id = response.last_voucher_id
        if response.raw_xml:
            raw_xml_parts.append(response.raw_xml)
        for message in response.messages:
            if message not in messages:
                messages.append(message)
    success = all(response.success for response in collected)
    return TallyResponse(
        success=success,
        created=sum(response.created for response in collected),
        altered=sum(response.altered for response in collected),
        errors=sum(response.errors for response in collected),
        exceptions=sum(response.exceptions for response in collected),
        cancelled=sum(response.cancelled for response in collected),
        last_voucher_id=last_voucher_id,
        messages=tuple(messages),
        raw_xml="\n".join(raw_xml_parts),
    )


def annotate_tally_response(response: TallyResponse, master_label: str) -> TallyResponse:
    """Add master context to a Tally response so failures name the exact master."""
    if response.messages:
        messages = tuple(f"{master_label} -> {message}" for message in response.messages)
    elif not response.success:
        messages = (f"{master_label} -> import failed",)
    else:
        messages = ()
    return TallyResponse(
        success=response.success,
        created=response.created,
        altered=response.altered,
        errors=response.errors,
        exceptions=response.exceptions,
        cancelled=response.cancelled,
        last_voucher_id=response.last_voucher_id,
        messages=messages,
        raw_xml=response.raw_xml,
    )


def validate_inventory_item_posting(data: InvoiceData) -> None:
    """Raise when reviewed line items are not complete enough for item export."""
    if not data.line_items:
        raise ValueError("Item posting requires at least one reviewed line item.")
    has_tax_detail = bool(gst_amount_details(data))
    issues: list[str] = []
    for index, item in enumerate(data.line_items, start=1):
        line_issues: list[str] = []
        if not ((item.item_name or item.description or "").strip()):
            line_issues.append("item name is missing")
        if item.quantity <= 0:
            line_issues.append("quantity must be greater than 0")
        if not (item.unit or "").strip():
            line_issues.append("unit is missing")
        if item.rate <= 0:
            line_issues.append("rate must be greater than 0")
        if item.taxable_value <= 0:
            line_issues.append("taxable value must be greater than 0")
        if not ((item.taxes and any(tax.tax_amount > 0 or tax.tax_rate > 0 for tax in item.taxes)) or has_tax_detail):
            line_issues.append("tax detail is missing")
        if line_issues:
            issues.append(f"Line {index}: " + ", ".join(line_issues))
    if issues:
        raise ValueError("Item posting requires complete reviewed line items.\n" + "\n".join(issues))



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



def build_tally_companies_xml() -> bytes:
    """Build a Tally collection export request for all company names."""
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "VERSION").text = "1"
    SubElement(header, "TALLYREQUEST").text = "Export"
    SubElement(header, "TYPE").text = "Collection"
    SubElement(header, "ID").text = "InvoiceAICompanies"
    body = SubElement(envelope, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    SubElement(static, "SVEXPORTFORMAT").text = "$$SysName:XML"
    tdl = SubElement(desc, "TDL")
    message = SubElement(tdl, "TDLMESSAGE")
    collection = SubElement(message, "COLLECTION", NAME="InvoiceAICompanies", ISMODIFY="No")
    SubElement(collection, "TYPE").text = "Company"
    SubElement(collection, "FETCH").text = "NAME"
    return tostring(envelope, encoding="utf-8", xml_declaration=True)


def build_tally_about_page_xml() -> bytes:
    """Build a Tally Product AboutPage export request for license information."""
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
