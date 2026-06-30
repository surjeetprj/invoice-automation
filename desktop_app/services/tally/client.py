from __future__ import annotations

"""HTTP/XML client for local TallyPrime direct posting."""

import logging
from typing import Iterable
from xml.etree.ElementTree import Element, SubElement, tostring

import requests

from ..settings import get_tally_settings
from ...domain.schemas import InvoiceData
from .lookup import TallyVoucherDetails, build_posted_voucher_lookup_xml, parse_posted_voucher_details
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
from .preflight import (
    TallyPreflight,
    annotate_tally_response,
    merge_tally_responses,
    normalize_name,
    normalized_names,
    parse_master_names,
    parse_master_details,
    prioritize_inventory_masters,
    validate_inventory_item_posting,
)
from .responses import TallyResponse, parse_tally_response
from .serial import (
    build_tally_about_page_xml,
    mask_serial,
    parse_tally_about_page_serial_number,
    tally_response_summary,
)
from .xml_utils import unique_collection_name
from .vouchers import build_inventory_purchase_voucher_xml, build_purchase_voucher_xml

logger = logging.getLogger(__name__)


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
        logger.info("Tally serial lookup started")
        logger.info("Tally serial probe started: Product AboutPage")
        raw = self.post_xml(build_tally_about_page_xml())
        serial = parse_tally_about_page_serial_number(raw)
        if serial:
            logger.info("Tally serial detected using Product AboutPage probe: %s", mask_serial(serial))
            return serial

        logger.error("Tally serial probe did not return a serial: Product AboutPage | response=%s", tally_response_summary(raw))
        raise ConnectionError(
            "Could not read TallyPrime serial number. Product AboutPage did not expose the TallyPrime serial number."
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

    def fetch_master_details(self, collection_name: str, master_type: str, company: str | None = None) -> list[dict[str, str]]:
        """Fetch master names along with their parent group names from TallyPrime."""
        xml = build_collection_export_xml(collection_name, master_type, company=company)
        raw = self.post_xml(xml)
        return parse_master_details(raw)

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

    def fetch_voucher_details(self, last_voucher_id: str, company: str | None = None) -> TallyVoucherDetails | None:
        """Fetch final Tally voucher fields for a posted voucher ID."""
        for id_field in ("MASTERID", "VOUCHERID"):
            xml = build_posted_voucher_lookup_xml(last_voucher_id, company=company, id_field=id_field)
            details = parse_posted_voucher_details(self.post_xml(xml))
            if details and details.voucher_number:
                return details
        return None

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


def build_tally_companies_xml() -> bytes:
    """Build a Tally collection export request for all company names."""
    unique_name = unique_collection_name("InvoiceAICompanies")
    envelope = Element("ENVELOPE")
    header = SubElement(envelope, "HEADER")
    SubElement(header, "VERSION").text = "1"
    SubElement(header, "TALLYREQUEST").text = "Export"
    SubElement(header, "TYPE").text = "Collection"
    SubElement(header, "ID").text = unique_name
    body = SubElement(envelope, "BODY")
    desc = SubElement(body, "DESC")
    static = SubElement(desc, "STATICVARIABLES")
    SubElement(static, "SVEXPORTFORMAT").text = "$$SysName:XML"
    tdl = SubElement(desc, "TDL")
    message = SubElement(tdl, "TDLMESSAGE")
    collection = SubElement(message, "COLLECTION", NAME=unique_name, ISMODIFY="No")
    SubElement(collection, "TYPE").text = "Company"
    SubElement(collection, "FETCH").text = "NAME"
    return tostring(envelope, encoding="utf-8", xml_declaration=True)
