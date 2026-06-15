from __future__ import annotations

"""HTTP/XML client for local TallyPrime direct posting."""

from dataclasses import dataclass
from typing import Iterable
from xml.etree import ElementTree

import requests

from ...config import TALLY_TIMEOUT_SECONDS, TALLY_URL
from ...domain.schemas import InvoiceData
from .masters import TallyMaster, build_collection_export_xml, build_master_import_xml, build_system_ledgers_xml, build_vendor_master_xml, required_purchase_masters
from .responses import TallyResponse, parse_tally_response
from .vouchers import build_purchase_voucher_xml


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

    def __init__(self, url: str = TALLY_URL, timeout: int = TALLY_TIMEOUT_SECONDS) -> None:
        self.url = url
        self.timeout = timeout

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

    def check_connection(self) -> None:
        """Raise if the TallyPrime HTTP endpoint cannot be reached."""
        self.fetch_master_names("InvoiceAIConnectionCheck", "Company")

    def fetch_master_names(self, collection_name: str, master_type: str) -> set[str]:
        """Fetch master names for a Tally collection type."""
        xml = build_collection_export_xml(collection_name, master_type)
        raw = self.post_xml(xml)
        return parse_master_names(raw)

    def preflight_purchase_invoice(self, data: InvoiceData) -> TallyPreflight:
        """Check which required purchase masters are missing in TallyPrime."""
        required = tuple(required_purchase_masters(data))
        ledger_names = self.fetch_master_names("InvoiceAILedgers", "Ledger")
        voucher_type_names = self.fetch_master_names("InvoiceAIVoucherTypes", "Voucher Type")
        missing: list[TallyMaster] = []
        for master in required:
            names = voucher_type_names if master.kind == "Voucher Type" else ledger_names
            if normalize_name(master.name) not in {normalize_name(name) for name in names}:
                missing.append(master)
        return TallyPreflight(required_masters=required, missing_masters=tuple(missing))

    def create_missing_masters(self, masters: Iterable[TallyMaster]) -> TallyResponse:
        """Create missing masters and return Tally's normalized response."""
        xml = build_master_import_xml(masters)
        return parse_tally_response(self.post_xml(xml))

    def sync_vendor_master(self, data: InvoiceData) -> TallyResponse:
        """Alter the vendor ledger with extracted mailing and tax details."""
        xml = build_vendor_master_xml(data, action="Alter")
        return parse_tally_response(self.post_xml(xml))

    def sync_system_ledgers(self) -> TallyResponse:
        """Alter purchase and GST tax ledgers with expected accounting metadata."""
        xml = build_system_ledgers_xml(action="Alter")
        return parse_tally_response(self.post_xml(xml))

    def post_purchase_voucher(self, invoice_id: int, data: InvoiceData) -> TallyResponse:
        """Post a ledger-only purchase voucher to TallyPrime."""
        xml = build_purchase_voucher_xml(invoice_id, data)
        return parse_tally_response(self.post_xml(xml))


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
