from __future__ import annotations

"""Regression tests for direct TallyPrime integration services."""

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from desktop_app.config import InvoiceStatus
from desktop_app.db.models import AuditLog, Base, Invoice
from desktop_app.db.repository import persist_extraction
from desktop_app.domain.schemas import InvoiceData, LineItem, TaxDetail, ValidationResult
from desktop_app.services.tally import TallyClient
from desktop_app.services.settings import TallySettings
from desktop_app.services.tally.client import TallyPreflight, annotate_tally_response, merge_tally_responses
from desktop_app.services.tally.lookup import (
    TallyVoucherDetails,
    build_posted_voucher_lookup_xml,
    parse_posted_voucher_details,
)
from desktop_app.services.tally.masters import (
    build_inventory_stock_items_xml,
    TallyMaster,
    build_master_import_xml,
    build_system_ledgers_xml,
    required_inventory_purchase_masters,
    required_purchase_masters,
)
from desktop_app.services.tally.responses import TallyResponse
from desktop_app.services.tally.vouchers import build_inventory_purchase_voucher_xml, build_purchase_voucher_xml
from desktop_app.services.workflow import DesktopWorkflow


class TallyServiceTests(unittest.TestCase):
    """Exercise XML builders, response parsing, and workflow posting."""

    def setUp(self) -> None:
        """Keep existing posting tests focused on Tally behavior."""
        self.workflow_settings_patch = patch(
            "desktop_app.services.workflow_tally.get_tally_settings",
            return_value=TallySettings(tally_company="Runtime Company"),
        )
        self.workflow_settings_patch.start()

    def tearDown(self) -> None:
        self.workflow_settings_patch.stop()

    def sample_invoice_data(self) -> InvoiceData:
        """Return a purchase invoice with GST totals."""
        return InvoiceData(
            invoice_number="PI-1",
            date="01-05-2026",
            vendor_name="Vendor Pvt Ltd",
            vendor_address="KH No. 76 Opp Plot No.1535, M.I.E Part-B, Bahadurgarh, Haryana 124507",
            vendor_gstin="09ABCDE1234F1Z5",
            vendor_state_code="09",
            vendor_pan="ABCDE1234F",
            vendor_contact="9999999999",
            customer_name="Customer Pvt Ltd",
            customer_gstin="09AAOCS7654P3Z5",
            place_of_supply="Uttar Pradesh",
            line_items=[
                LineItem(
                    item_name="Consulting Service",
                    description="Consulting Service",
                    hsn_sac="9983",
                    quantity=1,
                    unit="NOS",
                    rate=1000,
                    taxable_value=1000,
                    taxes=[
                        TaxDetail(tax_type="CGST", tax_rate=9, taxable_amount=1000, tax_amount=90),
                        TaxDetail(tax_type="SGST", tax_rate=9, taxable_amount=1000, tax_amount=90),
                    ],
                    total=1180,
                )
            ],
            total_taxable_amount=1000,
            total_cgst=90,
            total_sgst=90,
            total_tax_amount=180,
            total_amount=1180,
        )

    def make_engine(self):
        """Create an isolated in-memory SQLite engine."""
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        return engine

    def create_invoice(self, engine, status: str = InvoiceStatus.APPROVED) -> int:
        """Persist one invoice with extraction data and return its id."""
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = Invoice(filename="invoice.pdf", file_path="C:/tmp/invoice.pdf", status=status)
            db.add(invoice)
            db.flush()
            persist_extraction(db, invoice, self.sample_invoice_data(), ValidationResult(is_valid=True), "raw text")
            db.commit()
            return invoice.id

    def test_direct_tally_master_xml_uses_runtime_settings(self) -> None:
        """Direct Tally master XML should use runtime company and ledger names."""
        settings = TallySettings(
            tally_company="Runtime Company",
            tally_vendor_parent_ledger="Custom Creditors",
            purchase_ledger_name="Runtime Purchase",
            input_cgst_ledger_name="Runtime CGST",
            input_sgst_ledger_name="Runtime SGST",
            input_igst_ledger_name="Runtime IGST",
            input_cess_ledger_name="Runtime CESS",
        )
        with patch("desktop_app.services.tally.masters.get_tally_settings", return_value=settings):
            masters = required_purchase_masters(self.sample_invoice_data())
            xml = build_master_import_xml(masters).decode("utf-8")

        self.assertIn("<SVCURRENTCOMPANY>Runtime Company</SVCURRENTCOMPANY>", xml)
        self.assertIn('<LEDGER NAME="Runtime Purchase" ACTION="Create">', xml)
        self.assertIn('<LEDGER NAME="Runtime CGST" ACTION="Create">', xml)
        self.assertIn("<PARENT>Custom Creditors</PARENT>", xml)

    def test_direct_tally_voucher_xml_uses_runtime_settings(self) -> None:
        """Direct Tally voucher XML should use runtime company and ledger names."""
        settings = TallySettings(
            tally_company="Runtime Company",
            purchase_ledger_name="Runtime Purchase",
            input_cgst_ledger_name="Runtime CGST",
            input_sgst_ledger_name="Runtime SGST",
            input_igst_ledger_name="Runtime IGST",
            input_cess_ledger_name="Runtime CESS",
        )
        with patch("desktop_app.services.tally.vouchers.get_tally_settings", return_value=settings):
            xml = build_purchase_voucher_xml(1, self.sample_invoice_data()).decode("utf-8")

        self.assertIn("<SVCURRENTCOMPANY>Runtime Company</SVCURRENTCOMPANY>", xml)
        self.assertIn("<LEDGERNAME>Runtime Purchase</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Runtime CGST</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Runtime SGST</LEDGERNAME>", xml)

    def test_master_xml_includes_vendor_purchase_and_tax_ledgers(self) -> None:
        """Master XML should create controlled ledgers under the right parents."""
        masters = required_purchase_masters(self.sample_invoice_data())
        xml = build_master_import_xml(masters).decode("utf-8")
        self.assertIn('<LEDGER NAME="Vendor Pvt Ltd" ACTION="Create">', xml)
        self.assertIn("<PARENT>Sundry Creditors</PARENT>", xml)
        self.assertIn("<MAILINGNAME>Vendor Pvt Ltd</MAILINGNAME>", xml)
        self.assertIn("<ADDRESS>KH No. 76 Opp Plot No.1535</ADDRESS>", xml)
        self.assertIn("<LEDSTATENAME>Uttar Pradesh</LEDSTATENAME>", xml)
        self.assertIn("<COUNTRYNAME>India</COUNTRYNAME>", xml)
        self.assertIn("<PINCODE>124507</PINCODE>", xml)
        self.assertIn("<INCOMETAXNUMBER>ABCDE1234F</INCOMETAXNUMBER>", xml)
        self.assertIn("<PARTYGSTIN>09ABCDE1234F1Z5</PARTYGSTIN>", xml)
        self.assertIn("<GSTIN>09ABCDE1234F1Z5</GSTIN>", xml)
        self.assertIn("<LEDGERCONTACT>9999999999</LEDGERCONTACT>", xml)
        self.assertIn("<LEDMAILINGDETAILS.LIST>", xml)
        self.assertIn("<APPLICABLEFROM>20260401</APPLICABLEFROM>", xml)
        self.assertIn("<STATE>Uttar Pradesh</STATE>", xml)
        self.assertIn("<COUNTRY>India</COUNTRY>", xml)
        self.assertIn("<LEDGSTREGDETAILS.LIST>", xml)
        self.assertIn("<PLACEOFSUPPLY>Uttar Pradesh</PLACEOFSUPPLY>", xml)
        self.assertIn('<LEDGER NAME="Purchase Account" ACTION="Create">', xml)
        self.assertIn("<PARENT>Purchase Accounts</PARENT>", xml)
        self.assertIn('<LEDGER NAME="Input CGST" ACTION="Create">', xml)
        self.assertIn("<PARENT>Duties &amp; Taxes</PARENT>", xml)
        self.assertIn("<TAXTYPE>GST</TAXTYPE>", xml)
        self.assertIn("<GSTTYPE>CGST</GSTTYPE>", xml)
        self.assertIn("<GSTDUTYHEAD>CGST</GSTDUTYHEAD>", xml)
        self.assertIn("<ISINPUTCREDIT>Yes</ISINPUTCREDIT>", xml)

    def test_inventory_master_xml_uses_runtime_stock_group(self) -> None:
        """Item-wise Tally masters should use the runtime default stock group."""
        settings = TallySettings(default_stock_group="Software Services")
        with patch("desktop_app.services.tally.masters.get_tally_settings", return_value=settings):
            masters = required_inventory_purchase_masters(self.sample_invoice_data())
            xml = build_master_import_xml(masters).decode("utf-8")

        self.assertIn('<STOCKGROUP NAME="Software Services" ACTION="Create">', xml)
        self.assertIn("<PARENT>Software Services</PARENT>", xml)


    def test_inventory_master_xml_includes_unit_and_stock_item(self) -> None:
        """Inventory posting should preflight reviewed units and stock items."""
        masters = required_inventory_purchase_masters(self.sample_invoice_data())
        xml = build_master_import_xml(masters).decode("utf-8")
        self.assertIn('<STOCKGROUP NAME="Primary" ACTION="Create">', xml)
        self.assertIn('<UNIT NAME="NOS" RESERVEDNAME="" ACTION="Create">', xml)
        self.assertIn("<GSTREPUOM>NOS-NUMBERS</GSTREPUOM>", xml)
        self.assertIn("<REPORTINGUQCNAME>NOS-NUMBERS</REPORTINGUQCNAME>", xml)
        self.assertNotIn("<ORIGINALNAME>NOS</ORIGINALNAME>", xml)
        self.assertIn("<ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>", xml)
        self.assertIn('<STOCKITEM NAME="Consulting Service" ACTION="Create">', xml)
        self.assertIn("<PARENT>Primary</PARENT>", xml)
        self.assertIn("<BASEUNITS>NOS</BASEUNITS>", xml)
        self.assertIn("<VATBASEUNIT>NOS</VATBASEUNIT>", xml)
        self.assertIn("<HSNCODE>9983</HSNCODE>", xml)
        self.assertIn("<GSTHSNNAME>9983</GSTHSNNAME>", xml)
        self.assertIn("<GSTTYPEOFSUPPLY>Services</GSTTYPEOFSUPPLY>", xml)
        self.assertIn("<GSTDETAILS.LIST>", xml)
        self.assertIn("<HSNDETAILS.LIST>", xml)
        self.assertIn("<SRCOFHSNDETAILS>Specify Details Here</SRCOFHSNDETAILS>", xml)
        self.assertIn("<DESCRIPTION>Consulting Service</DESCRIPTION>", xml)
        self.assertIn("<SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>", xml)
        self.assertIn("<STATENAME>\x04 Any</STATENAME>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATE>18</GSTRATE>", xml)
        self.assertIn("<GSTRATE>9</GSTRATE>", xml)
        self.assertIn("<GSTRATEPERUNIT>0</GSTRATEPERUNIT>", xml)

    def test_inventory_required_masters_include_stock_group_before_stock_items(self) -> None:
        """Item posting should create the stock group before dependent stock items."""
        masters = required_inventory_purchase_masters(self.sample_invoice_data())
        labels = [master.label for master in masters]
        self.assertIn("Stock Group Master: Primary", labels)
        self.assertLess(labels.index("Stock Group Master: Primary"), labels.index("Stock Item Master: Consulting Service under Primary"))

    def test_inventory_unit_xml_maps_pcs_to_pieces_uqc(self) -> None:
        """Default PCS units should emit the GST pieces UQC for TallyPrime."""
        data = self.sample_invoice_data().model_copy(
            update={
                "line_items": [
                    self.sample_invoice_data().line_items[0].model_copy(update={"unit": "PCS"})
                ]
            }
        )
        masters = required_inventory_purchase_masters(data)
        labels = [master.label for master in masters]
        xml = build_master_import_xml(masters).decode("utf-8")
        self.assertIn("Unit Master: PCS", labels)
        self.assertIn('<UNIT NAME="PCS" RESERVEDNAME="" ACTION="Create">', xml)
        self.assertIn("<GSTREPUOM>PCS-PIECES</GSTREPUOM>", xml)
        self.assertIn("<REPORTINGUQCNAME>PCS-PIECES</REPORTINGUQCNAME>", xml)

    def test_inventory_unit_xml_maps_pairs_to_prs_uqc(self) -> None:
        """Pair-based reviewed units should emit a valid GST reporting UQC."""
        data = self.sample_invoice_data().model_copy(
            update={
                "line_items": [
                    self.sample_invoice_data().line_items[0].model_copy(update={"unit": "PRS"})
                ]
            }
        )
        xml = build_master_import_xml(required_inventory_purchase_masters(data)).decode("utf-8")
        self.assertIn('<UNIT NAME="PRS" RESERVEDNAME="" ACTION="Create">', xml)
        self.assertIn("<GSTREPUOM>PRS-PAIRS</GSTREPUOM>", xml)
        self.assertIn("<REPORTINGUQCNAME>PRS-PAIRS</REPORTINGUQCNAME>", xml)
        self.assertNotIn("<ORIGINALNAME>PRS</ORIGINALNAME>", xml)

    def test_inventory_stock_item_sync_xml_alters_items_with_gst_details(self) -> None:
        """Stock item sync should alter reviewed items with GST and HSN metadata."""
        xml = build_inventory_stock_items_xml(self.sample_invoice_data()).decode("utf-8")
        self.assertIn('<STOCKITEM NAME="Consulting Service" ACTION="Alter">', xml)
        self.assertIn("<HSNCODE>9983</HSNCODE>", xml)
        self.assertIn("<GSTHSNNAME>9983</GSTHSNNAME>", xml)
        self.assertIn("<GSTDETAILS.LIST>", xml)
        self.assertIn("<HSNDETAILS.LIST>", xml)
        self.assertIn("<SRCOFHSNDETAILS>Specify Details Here</SRCOFHSNDETAILS>", xml)
        self.assertIn("<SRCOFGSTDETAILS>Specify Details Here</SRCOFGSTDETAILS>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>State Cess</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATE>18</GSTRATE>", xml)
        self.assertIn("<GSTRATE>0</GSTRATE>", xml)
        self.assertIn("<GSTRATEPERUNIT>0</GSTRATEPERUNIT>", xml)
        self.assertIn("<TEMPGSTITEMSLABRATES.LIST />", xml)

    def test_system_ledger_xml_alters_gst_tax_ledgers(self) -> None:
        """System ledger sync should enrich existing GST ledgers."""
        xml = build_system_ledgers_xml().decode("utf-8")
        self.assertIn('<LEDGER NAME="Input CGST" ACTION="Alter">', xml)
        self.assertIn('<LEDGER NAME="Input SGST" ACTION="Alter">', xml)
        self.assertIn('<LEDGER NAME="Input IGST" ACTION="Alter">', xml)
        self.assertIn("<GSTTYPE>CGST</GSTTYPE>", xml)
        self.assertIn("<GSTTYPE>SGST/UTGST</GSTTYPE>", xml)
        self.assertIn("<GSTTYPE>IGST</GSTTYPE>", xml)
        self.assertIn("<APPROPRIATEFOR>Input Tax Credit</APPROPRIATEFOR>", xml)

    def test_direct_purchase_voucher_is_ledger_only(self) -> None:
        """Direct posting voucher should avoid inventory entries in v1."""
        xml = build_purchase_voucher_xml(1, self.sample_invoice_data()).decode("utf-8")
        self.assertIn('VCHTYPE="Purchase"', xml)
        self.assertIn('OBJVIEW="Accounting Voucher View"', xml)
        self.assertIn('<DATE TYPE="Date">20260501</DATE>', xml)
        self.assertIn('<EFFECTIVEDATE TYPE="Date">20260501</EFFECTIVEDATE>', xml)
        self.assertLess(xml.index('<DATE TYPE="Date">20260501</DATE>'), xml.index("<VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>"))
        self.assertIn("<PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>", xml)
        self.assertIn("<LEDGERNAME>Vendor Pvt Ltd</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Purchase Account</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Input CGST</LEDGERNAME>", xml)
        self.assertIn("<REFERENCEDATE TYPE=\"Date\">20260501</REFERENCEDATE>", xml)
        self.assertIn("<PARTYINVNO>PI-1</PARTYINVNO>", xml)
        self.assertIn("<ISGSTOVERRIDDEN>Yes</ISGSTOVERRIDDEN>", xml)
        self.assertIn("<VCHGSTSTATUSISOVERRDN>Yes</VCHGSTSTATUSISOVERRDN>", xml)
        self.assertIn("<GSTOVRDNTAXABILITY>Taxable</GSTOVRDNTAXABILITY>", xml)
        self.assertIn("<GSTOVRDNASSESSABLEVALUE>1000.00</GSTOVRDNASSESSABLEVALUE>", xml)
        self.assertIn("<GSTOVRDNINELIGIBLEITC>No</GSTOVRDNINELIGIBLEITC>", xml)
        self.assertIn("<HSNCODE>9983</HSNCODE>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>Central Tax</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>State Tax</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATE>9.00</GSTRATE>", xml)
        self.assertIn("<GSTOVRDNTAXAMOUNT>90.00</GSTOVRDNTAXAMOUNT>", xml)
        self.assertNotIn("ALLINVENTORYENTRIES.LIST", xml)

    def test_inventory_purchase_voucher_uses_inventory_entries(self) -> None:
        """Item posting should emit an invoice-mode voucher with inventory rows."""
        xml = build_inventory_purchase_voucher_xml(1, self.sample_invoice_data()).decode("utf-8")
        self.assertIn('OBJVIEW="Invoice Voucher View"', xml)
        self.assertIn("<PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>", xml)
        self.assertIn("<ISINVOICE>Yes</ISINVOICE>", xml)
        self.assertIn("<VCHENTRYMODE>Item Invoice</VCHENTRYMODE>", xml)
        self.assertIn("<ALLINVENTORYENTRIES.LIST>", xml)
        self.assertIn("<STOCKITEMNAME>Consulting Service</STOCKITEMNAME>", xml)
        self.assertIn("<GSTSOURCETYPE>Stock Item</GSTSOURCETYPE>", xml)
        self.assertIn("<HSNSOURCETYPE>Stock Item</HSNSOURCETYPE>", xml)
        self.assertIn("<ACTUALQTY>1 Nos</ACTUALQTY>", xml)
        self.assertIn("<BILLEDQTY>1 Nos</BILLEDQTY>", xml)
        self.assertIn("<RATE>1000.00/Nos</RATE>", xml)
        self.assertIn("<AMOUNT>-1000.00</AMOUNT>", xml)
        self.assertIn("<BATCHALLOCATIONS.LIST>", xml)
        self.assertIn("<ACCOUNTINGALLOCATIONS.LIST>", xml)
        self.assertIn("<LEDGERNAME>Purchase Account</LEDGERNAME>", xml)
        self.assertIn("<HSNCODE>9983</HSNCODE>", xml)
        self.assertIn("<LEDGERENTRIES.LIST>", xml)
        self.assertIn("<LEDGERNAME>Input CGST</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Input SGST</LEDGERNAME>", xml)
        self.assertIn("<ADDLALLOCTYPE>Appropriate by condition</ADDLALLOCTYPE>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>State Tax</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>CGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>SGST/UTGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>Cess</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATEDUTYHEAD>State Cess</GSTRATEDUTYHEAD>", xml)
        self.assertIn("<GSTRATE>18</GSTRATE>", xml)

    def test_inventory_purchase_voucher_prefers_clean_item_name(self) -> None:
        """Direct item-wise posting should use item_name for Tally stock item names."""
        item = self.sample_invoice_data().line_items[0].model_copy(
            update={
                "item_name": "VPS Custom Configuration",
                "description": "VPS Custom Configuration 1 Year Plan Username : user HSN: 997315",
                "hsn_sac": "997315",
            }
        )
        data = self.sample_invoice_data().model_copy(update={"line_items": [item]})

        xml = build_inventory_purchase_voucher_xml(1, data).decode("utf-8")

        self.assertIn("<STOCKITEMNAME>VPS Custom Configuration</STOCKITEMNAME>", xml)
        self.assertIn("<GSTITEMSOURCE>VPS Custom Configuration</GSTITEMSOURCE>", xml)
        self.assertNotIn("<STOCKITEMNAME>VPS Custom Configuration 1 Year Plan", xml)

    def test_inventory_stock_item_master_prefers_clean_item_name(self) -> None:
        """TallyPrime stock item masters should use item_name while preserving fallback."""
        item = self.sample_invoice_data().line_items[0].model_copy(
            update={
                "item_name": "Clean Service",
                "description": "Clean Service detailed support renewal HSN: 9983",
            }
        )
        data = self.sample_invoice_data().model_copy(update={"line_items": [item]})

        xml = build_inventory_stock_items_xml(data).decode("utf-8")

        self.assertIn('<STOCKITEM NAME="Clean Service" ACTION="Alter">', xml)
        self.assertNotIn('<STOCKITEM NAME="Clean Service detailed support renewal', xml)

    def test_inventory_stock_item_name_falls_back_to_description(self) -> None:
        """TallyPrime item-wise posting should still work when item_name is blank."""
        item = self.sample_invoice_data().line_items[0].model_copy(
            update={"item_name": None, "description": "Fallback Service"}
        )
        data = self.sample_invoice_data().model_copy(update={"line_items": [item]})

        xml = build_inventory_purchase_voucher_xml(1, data).decode("utf-8")

        self.assertIn("<STOCKITEMNAME>Fallback Service</STOCKITEMNAME>", xml)

    def test_direct_purchase_voucher_skips_gst_override_without_vendor_gstin(self) -> None:
        """Tally should receive accounting tax ledgers when party GSTIN is missing."""
        data = self.sample_invoice_data().model_copy(update={"vendor_gstin": None})
        xml = build_purchase_voucher_xml(1, data).decode("utf-8")
        self.assertIn("<LEDGERNAME>Input CGST</LEDGERNAME>", xml)
        self.assertIn("<LEDGERNAME>Input SGST</LEDGERNAME>", xml)
        self.assertNotIn("<PARTYGSTIN", xml)
        self.assertNotIn("<ISGSTOVERRIDDEN>Yes</ISGSTOVERRIDDEN>", xml)
        self.assertNotIn("<GSTDETAILS.LIST>", xml)

    def test_parse_master_names_reads_exported_tally_names(self) -> None:
        """Collection exports should provide names for preflight matching."""
        xml = """
        <ENVELOPE><BODY><DATA>
          <LEDGER NAME="Vendor Pvt Ltd"><NAME>Vendor Pvt Ltd</NAME></LEDGER>
          <LEDGER><NAME>Purchase Account</NAME></LEDGER>
          <UNIT NAME="YR"><NAME>YR</NAME><FORMALNAME>Year</FORMALNAME></UNIT>
          <UNIT NAME="BOX"><NAME>BOX</NAME><MAILINGNAME>Box Name</MAILINGNAME></UNIT>
        </DATA></BODY></ENVELOPE>
        """
        from desktop_app.services.tally.client import parse_master_names

        names = parse_master_names(xml)
        self.assertIn("Vendor Pvt Ltd", names)
        self.assertIn("Purchase Account", names)
        self.assertIn("YR", names)
        self.assertIn("Year", names)
        self.assertIn("BOX", names)
        self.assertIn("Box Name", names)


    def test_parse_master_names_sanitizes_invalid_xml_without_dropping_valid_entities(self) -> None:
        """Tally XML parsing should remove invalid references while preserving valid text."""
        from desktop_app.services.tally.client import parse_master_names
        xml = """<ENVELOPE><BODY><DATA><COLLECTION>
        <LEDGER NAME="A &amp; B Services" />
        <LEDGER NAME="Invalid &#4; Control" />
        </COLLECTION></DATA></BODY></ENVELOPE>"""
        names = parse_master_names(xml)
        self.assertIn("A & B Services", names)
        self.assertIn("Invalid  Control", names)

    def test_posted_voucher_lookup_xml_filters_by_master_id(self) -> None:
        """Voucher lookup should request the posted voucher by Tally master ID."""
        xml = build_posted_voucher_lookup_xml("101", company="Runtime Company").decode("utf-8")

        self.assertIn("<TYPE>Voucher</TYPE>", xml)
        self.assertIn("<SVCURRENTCOMPANY>Runtime Company</SVCURRENTCOMPANY>", xml)
        self.assertIn("<FETCH>VOUCHERNUMBER,VOUCHERTYPENAME,DATE,PARTYINVNO,REFERENCE,MASTERID,VOUCHERID</FETCH>", xml)
        self.assertIn("<FILTERS>BahiAIVoucherByMasterId</FILTERS>", xml)
        self.assertIn("$MASTERID = 101", xml)

        voucher_id_xml = build_posted_voucher_lookup_xml("101", company="Runtime Company", id_field="VOUCHERID").decode("utf-8")
        self.assertIn("$VOUCHERID = 101", voucher_id_xml)

    def test_parse_posted_voucher_details_reads_purchase_voucher_number(self) -> None:
        """Voucher lookup parser should extract Tally's final purchase voucher number."""
        details = parse_posted_voucher_details(
            """
            <ENVELOPE><BODY><DATA><COLLECTION>
              <VOUCHER>
                <DATE>20260630</DATE>
                <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
                <VOUCHERNUMBER>27</VOUCHERNUMBER>
                <PARTYINVNO>SUP-1</PARTYINVNO>
                <REFERENCE>SUP-1</REFERENCE>
                <MASTERID>101</MASTERID>
              </VOUCHER>
            </COLLECTION></DATA></BODY></ENVELOPE>
            """
        )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.voucher_number, "27")
        self.assertEqual(details.voucher_type, "Purchase")
        self.assertEqual(details.party_invoice_number, "SUP-1")
        self.assertEqual(details.master_id, "101")

    def test_parse_posted_voucher_details_handles_empty_or_bad_response(self) -> None:
        """Bad voucher lookup responses should not crash posting."""
        self.assertIsNone(parse_posted_voucher_details("not xml"))
        self.assertIsNone(parse_posted_voucher_details("<ENVELOPE><BODY /></ENVELOPE>"))

    def test_parse_posted_voucher_details_ignores_cmpinfo_voucher_count(self) -> None:
        """Tally responses include CMPINFO voucher counts before the actual voucher object."""
        details = parse_posted_voucher_details(
            """
            <ENVELOPE><BODY>
              <DESC><CMPINFO><VOUCHER>16</VOUCHER></CMPINFO></DESC>
              <DATA><COLLECTION>
                <VOUCHER VCHTYPE="Purchase" OBJVIEW="Invoice Voucher View">
                  <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
                  <VOUCHERNUMBER>PUR/047</VOUCHERNUMBER>
                  <REFERENCE TYPE="String">SIS/26-27/013</REFERENCE>
                  <PARTYINVNO TYPE="String">SIS/26-27/013</PARTYINVNO>
                  <MASTERID TYPE="Number"> 47</MASTERID>
                  <VOUCHERID TYPE="Number"> 57</VOUCHERID>
                </VOUCHER>
              </COLLECTION></DATA>
            </BODY></ENVELOPE>
            """
        )

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.voucher_number, "PUR/047")
        self.assertEqual(details.master_id, "47")

    def test_tally_client_retries_voucher_lookup_with_voucher_id(self) -> None:
        """LASTVCHID may match Tally's voucher ID rather than the master ID filter."""
        client = TallyClient()
        empty_response = "<ENVELOPE><BODY><DATA><COLLECTION /></DATA></BODY></ENVELOPE>"
        voucher_response = """
        <ENVELOPE><BODY><DATA><COLLECTION>
          <VOUCHER><VOUCHERNUMBER>27</VOUCHERNUMBER><VOUCHERID>44</VOUCHERID></VOUCHER>
        </COLLECTION></DATA></BODY></ENVELOPE>
        """
        with patch.object(client, "post_xml", side_effect=[empty_response, voucher_response]) as post_xml:
            details = client.fetch_voucher_details("44", company="Runtime Company")

        self.assertIsNotNone(details)
        assert details is not None
        self.assertEqual(details.voucher_number, "27")
        self.assertEqual(post_xml.call_count, 2)
        self.assertIn(b"$MASTERID = 44", post_xml.call_args_list[0].args[0])
        self.assertIn(b"$VOUCHERID = 44", post_xml.call_args_list[1].args[0])

    def test_build_collection_export_xml_for_unit(self) -> None:
        """Collection export XML for Unit master type should fetch both NAME and FORMALNAME."""
        from desktop_app.services.tally.masters import build_collection_export_xml
        xml = build_collection_export_xml("BahiAIUnits", "Unit").decode("utf-8")
        self.assertIn("<FETCH>NAME</FETCH>", xml)
        self.assertIn("<FETCH>FORMALNAME</FETCH>", xml)

        # For non-Unit collections, it should not fetch FORMALNAME
        xml_ledger = build_collection_export_xml("BahiAILedgers", "Ledger").decode("utf-8")
        self.assertIn("<FETCH>NAME</FETCH>", xml_ledger)
        self.assertNotIn("<FETCH>FORMALNAME</FETCH>", xml_ledger)

    def test_inventory_master_creation_is_one_by_one_before_stock_items(self) -> None:
        """Inventory prerequisites should be created one by one before dependent stock items."""
        client = TallyClient()
        masters = (
            TallyMaster("Primary", "Stock Group Master"),
            TallyMaster("PRS", "Unit Master"),
            TallyMaster("Pair Item", "Stock Item Master", parent="Primary", unit_name="PRS"),
        )
        with patch.object(
            client,
            "create_missing_masters",
            side_effect=[
                TallyResponse(success=True, created=1),
                TallyResponse(success=True, created=1),
                TallyResponse(success=True, created=1),
            ],
        ) as create_missing:
            response = client.create_missing_inventory_masters(masters)

        self.assertTrue(response.success)
        self.assertEqual(response.created, 3)
        self.assertEqual(create_missing.call_count, 3)
        batches = [call.args[0] for call in create_missing.call_args_list]
        self.assertTrue(all(len(batch) == 1 for batch in batches))
        self.assertEqual(batches[0][0].kind, "Stock Group Master")
        self.assertEqual(batches[1][0].kind, "Unit Master")
        self.assertEqual(batches[2][0].kind, "Stock Item Master")

    def test_merge_tally_responses_accumulates_counts_and_messages(self) -> None:
        """Staged inventory imports should surface a single combined result."""
        merged = merge_tally_responses(
            [
                TallyResponse(success=True, created=1),
                TallyResponse(success=False, exceptions=1, messages=("Unit 'PRS' does not exist!",)),
            ]
        )
        self.assertFalse(merged.success)
        self.assertEqual(merged.created, 1)
        self.assertEqual(merged.exceptions, 1)
        self.assertIn("Unit 'PRS' does not exist!", merged.messages)

    def test_inventory_master_creation_failure_names_exact_master(self) -> None:
        """Inventory master failures should identify the exact failing master."""
        client = TallyClient()
        masters = (
            TallyMaster("Primary", "Stock Group Master"),
            TallyMaster("PRS", "Unit Master"),
        )
        with patch.object(
            client,
            "create_missing_masters",
            side_effect=[
                TallyResponse(success=True, created=1),
                TallyResponse(success=False, exceptions=1, messages=("DUPLICATE ORIGINAL NAME",)),
            ],
        ):
            response = client.create_missing_inventory_masters(masters)

        self.assertFalse(response.success)
        self.assertIn("Unit Master: PRS -> DUPLICATE ORIGINAL NAME", response.messages)

    def test_annotate_tally_response_adds_master_context(self) -> None:
        """Master context should be preserved on both success and failure responses."""
        failure = annotate_tally_response(
            TallyResponse(success=False, exceptions=1, messages=("DUPLICATE ORIGINAL NAME",)),
            "Unit Master: PRS",
        )
        success = annotate_tally_response(TallyResponse(success=True, created=1), "Unit Master: PRS")
        self.assertEqual(failure.messages, ("Unit Master: PRS -> DUPLICATE ORIGINAL NAME",))
        self.assertEqual(success.messages, ())

    def test_workflow_lists_tally_ledgers_and_stock_groups_for_company(self) -> None:
        """Settings lookups should fetch Tally masters for the requested company."""
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.TallyClient") as client_cls:
            client = client_cls.return_value
            client.fetch_master_names.side_effect = [{"Purchase", "Input CGST"}, {"Primary", "Licenses"}]
            ledgers = workflow.list_tally_ledgers("SRC Pvt Ltd")
            stock_groups = workflow.list_tally_stock_groups("SRC Pvt Ltd")

        self.assertEqual(ledgers, ["Input CGST", "Purchase"])
        self.assertEqual(stock_groups, ["Licenses", "Primary"])
        client.fetch_master_names.assert_any_call("BahiAISettingsLedgers", "Ledger", company="SRC Pvt Ltd")
        client.fetch_master_names.assert_any_call("BahiAISettingsStockGroups", "Stock Group", company="SRC Pvt Ltd")

    def test_workflow_lists_tally_options(self) -> None:
        """list_tally_options should query groups and ledgers and filter/categorize them by parent group."""
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.TallyClient") as client_cls:
            client = client_cls.return_value
            client.fetch_master_details.side_effect = [
                # Groups
                [
                    {"name": "Sundry Creditors", "parent": "Current Liabilities"},
                    {"name": "Local Creditors", "parent": "Sundry Creditors"},
                    {"name": "Duties & Taxes", "parent": "Current Liabilities"},
                    {"name": "Purchase Accounts", "parent": "Primary"},
                ],
                # Ledgers
                [
                    {"name": "Supplier A", "parent": "Local Creditors"},
                    {"name": "Supplier B", "parent": "Sundry Creditors"},
                    {"name": "Purchase Account", "parent": "Purchase Accounts"},
                    {"name": "Input CGST", "parent": "Duties & Taxes"},
                    {"name": "Cash", "parent": "Cash-in-Hand"},
                ]
            ]
            client.fetch_master_names.return_value = {"Primary", "Services"}
            
            options = workflow.list_tally_options("SRC Pvt Ltd")
            
            self.assertEqual(options["groups"], ["Local Creditors", "Sundry Creditors"])
            self.assertEqual(options["purchase_ledgers"], ["Purchase Account"])
            self.assertEqual(options["duty_ledgers"], ["Input CGST"])
            self.assertEqual(options["stock_groups"], ["Primary", "Services"])

    def test_workflow_posts_approved_invoice_and_marks_posted(self) -> None:
        """Successful Tally posting should mark the invoice Posted and audit it."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.check_connection.return_value = None
                client.preflight_purchase_invoice.return_value = TallyPreflight((), ())
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.post_purchase_voucher.return_value = TallyResponse(success=True, created=1, last_voucher_id="101")
                client.fetch_voucher_details.return_value = TallyVoucherDetails(voucher_number="27", master_id="101")
                result = workflow.post_invoice_to_tally(invoice_id)

        self.assertTrue(result["success"])
        self.assertEqual(result["last_voucher_id"], "101")
        self.assertEqual(result["purchase_voucher_number"], "27")
        self.assertIsNone(result["warning"])
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.POSTED)
            logs = db.scalars(select(AuditLog).where(AuditLog.invoice_id == invoice_id)).all()
            self.assertTrue(any("Pushed to TallyPrime" in log.action for log in logs))
            self.assertTrue(any("Purchase Voucher Number: 27" in log.action and "Last voucher ID: 101" in log.action for log in logs))

    def test_workflow_posts_itemwise_invoice_and_marks_posted(self) -> None:
        """Successful inventory Tally posting should mark the invoice Posted and audit it."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.preflight_inventory_purchase_invoice.return_value = TallyPreflight((), ())
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.sync_inventory_item_masters.return_value = TallyResponse(success=True, altered=1)
                client.post_inventory_purchase_voucher.return_value = TallyResponse(success=True, created=1, last_voucher_id="202")
                client.fetch_voucher_details.return_value = TallyVoucherDetails(voucher_number="28", master_id="202")
                result = workflow.post_invoice_items_to_tally(invoice_id)

        self.assertTrue(result["success"])
        self.assertEqual(result["last_voucher_id"], "202")
        self.assertEqual(result["purchase_voucher_number"], "28")
        self.assertIsNone(result["warning"])
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.POSTED)
            logs = db.scalars(select(AuditLog).where(AuditLog.invoice_id == invoice_id)).all()
            self.assertTrue(any("item-wise to TallyPrime" in log.action for log in logs))
            self.assertTrue(any("Purchase Voucher Number: 28" in log.action and "Last voucher ID: 202" in log.action for log in logs))

    def test_workflow_keeps_invoice_posted_when_purchase_voucher_lookup_fails(self) -> None:
        """Voucher lookup failure should not undo a successful Tally posting."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.check_connection.return_value = None
                client.preflight_purchase_invoice.return_value = TallyPreflight((), ())
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.post_purchase_voucher.return_value = TallyResponse(success=True, created=1, last_voucher_id="101")
                client.fetch_voucher_details.side_effect = RuntimeError("lookup unavailable")
                result = workflow.post_invoice_to_tally(invoice_id)

        self.assertTrue(result["success"])
        self.assertIsNone(result["purchase_voucher_number"])
        self.assertIn("Purchase Voucher Number could not be fetched", result["warning"])
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.POSTED)
            logs = db.scalars(select(AuditLog).where(AuditLog.invoice_id == invoice_id)).all()
            self.assertTrue(any("voucher lookup failed" in log.action and "Last voucher ID: 101" in log.action for log in logs))

    def test_workflow_requires_confirmation_for_missing_masters(self) -> None:
        """Missing masters should be reported before creating them."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        missing = (TallyMaster("Vendor Pvt Ltd", "Vendor Ledger", "Sundry Creditors"),)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.check_connection.return_value = None
                client.preflight_purchase_invoice.return_value = TallyPreflight(missing, missing)
                result = workflow.post_invoice_to_tally(invoice_id, create_missing_masters=False)

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_confirmation"])
        self.assertIn("Vendor Ledger: Vendor Pvt Ltd under Sundry Creditors", result["missing_masters"])
        client.create_missing_masters.assert_not_called()
        client.post_purchase_voucher.assert_not_called()

    def test_workflow_requires_confirmation_for_missing_inventory_masters(self) -> None:
        """Missing units or stock items should be reported before item posting."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        missing = (
            TallyMaster("NOS", "Unit Master"),
            TallyMaster("Consulting Service", "Stock Item Master", parent="Primary", unit_name="NOS"),
        )
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.preflight_inventory_purchase_invoice.return_value = TallyPreflight(missing, missing)
                result = workflow.post_invoice_items_to_tally(invoice_id, create_missing_masters=False)

        self.assertFalse(result["success"])
        self.assertTrue(result["requires_confirmation"])
        self.assertIn("Unit Master: NOS", result["missing_masters"])
        self.assertIn("Stock Item Master: Consulting Service under Primary", result["missing_masters"])
        client.create_missing_inventory_masters.assert_not_called()
        client.post_inventory_purchase_voucher.assert_not_called()

    def test_workflow_creates_confirmed_masters_before_posting(self) -> None:
        """Confirmed missing masters should be created before voucher posting."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        missing = (TallyMaster("Vendor Pvt Ltd", "Vendor Ledger", "Sundry Creditors"),)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.check_connection.return_value = None
                client.preflight_purchase_invoice.return_value = TallyPreflight(missing, missing)
                client.create_missing_masters.return_value = TallyResponse(success=True, created=1)
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.post_purchase_voucher.return_value = TallyResponse(success=True, created=1)
                result = workflow.post_invoice_to_tally(invoice_id, create_missing_masters=True)

        self.assertTrue(result["success"])
        client.create_missing_masters.assert_called_once_with(missing)
        client.sync_vendor_master.assert_called_once()
        client.sync_system_ledgers.assert_called_once()
        client.post_purchase_voucher.assert_called_once()

    def test_workflow_creates_confirmed_inventory_masters_before_item_posting(self) -> None:
        """Confirmed missing units and stock items should be created before item posting."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        missing = (
            TallyMaster("NOS", "Unit Master"),
            TallyMaster("Consulting Service", "Stock Item Master", parent="Primary", unit_name="NOS"),
        )
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.preflight_inventory_purchase_invoice.return_value = TallyPreflight(missing, missing)
                client.create_missing_inventory_masters.return_value = TallyResponse(success=True, created=2)
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.sync_inventory_item_masters.return_value = TallyResponse(success=True, altered=1)
                client.post_inventory_purchase_voucher.return_value = TallyResponse(success=True, created=1)
                result = workflow.post_invoice_items_to_tally(invoice_id, create_missing_masters=True)

        self.assertTrue(result["success"])
        client.create_missing_inventory_masters.assert_called_once_with(missing)
        client.sync_inventory_item_masters.assert_called_once()
        client.post_inventory_purchase_voucher.assert_called_once()

    def test_workflow_syncs_vendor_master_without_posting_voucher(self) -> None:
        """Vendor master sync should update Tally without creating a voucher."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.check_connection.return_value = None
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                result = workflow.sync_vendor_master_to_tally(invoice_id)

        self.assertTrue(result["success"])
        client.sync_vendor_master.assert_called_once()
        client.post_purchase_voucher.assert_not_called()

    def test_workflow_syncs_system_ledgers_without_posting_voucher(self) -> None:
        """GST ledger sync should update Tally ledgers without creating a voucher."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.check_connection.return_value = None
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                result = workflow.sync_tally_system_ledgers(invoice_id)

        self.assertTrue(result["success"])
        client.sync_system_ledgers.assert_called_once()
        client.post_purchase_voucher.assert_not_called()

    def test_workflow_tally_failure_keeps_invoice_approved(self) -> None:
        """Failed voucher posting must not mark the invoice Posted."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.check_connection.return_value = None
                client.preflight_purchase_invoice.return_value = TallyPreflight((), ())
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.post_purchase_voucher.return_value = TallyResponse(success=False, errors=1, messages=("Missing ledger",))
                with self.assertRaises(ValueError):
                    workflow.post_invoice_to_tally(invoice_id)

        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.APPROVED)

    def test_workflow_inventory_tally_failure_keeps_invoice_approved(self) -> None:
        """Failed inventory voucher posting must not mark the invoice Posted."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.preflight_inventory_purchase_invoice.return_value = TallyPreflight((), ())
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.sync_inventory_item_masters.return_value = TallyResponse(success=True, altered=1)
                client.post_inventory_purchase_voucher.return_value = TallyResponse(success=False, exceptions=1, messages=("Item error",))
                with self.assertRaises(ValueError):
                    workflow.post_invoice_items_to_tally(invoice_id)

        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.APPROVED)

    def test_workflow_inventory_posting_rejects_incomplete_line_items_before_tally(self) -> None:
        """Item posting should fail fast when reviewed line data is incomplete."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            assert invoice is not None
            persist_extraction(
                db,
                invoice,
                self.sample_invoice_data().model_copy(
                    update={
                        "line_items": [
                            LineItem(
                                description="Broken item",
                                quantity=0.0,
                                unit="",
                                rate=0.0,
                                taxable_value=0.0,
                                taxes=[],
                            )
                        ]
                    }
                ),
                ValidationResult(is_valid=True),
                "raw text",
            )
            db.commit()

        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Runtime Company"}
                client.preflight_inventory_purchase_invoice.side_effect = ValueError(
                    "Item posting requires complete reviewed line items.\nLine 1: quantity must be greater than 0"
                )
                with self.assertRaises(ValueError):
                    workflow.post_invoice_items_to_tally(invoice_id)
                client.post_inventory_purchase_voucher.assert_not_called()

    def test_workflow_blocks_tally_post_when_company_not_selected(self) -> None:
        """Direct Tally posting should require an explicit selected company."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow_tally.get_tally_settings", return_value=TallySettings(tally_company="")):
            with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
                with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                    client = client_cls.return_value
                    with self.assertRaisesRegex(ValueError, "Select a TallyPrime company"):
                        workflow.post_invoice_to_tally(invoice_id)

        client.fetch_company_names.assert_not_called()
        client.fetch_tally_serial_number.assert_not_called()
        client.preflight_purchase_invoice.assert_not_called()

    def test_workflow_blocks_tally_post_when_selected_company_is_not_open(self) -> None:
        """Direct Tally posting should stop when Tally does not return the selected company."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_company_names.return_value = {"Other Company"}
                with self.assertRaisesRegex(ValueError, "Selected TallyPrime company was not found"):
                    workflow.post_invoice_to_tally(invoice_id)

        client.fetch_tally_serial_number.assert_not_called()
        client.preflight_purchase_invoice.assert_not_called()
        client.create_missing_masters.assert_not_called()
        client.post_purchase_voucher.assert_not_called()

    def test_downloadable_exports_do_not_check_tally_connection(self) -> None:
        """File-based exports should remain available without direct Tally connection checks."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            for fmt in ("json", "tally"):
                content, filename = workflow.export_invoice(invoice_id, fmt)
                self.assertIsNotNone(filename)
                self.assertTrue(content)

    def test_removed_file_exports_are_unsupported(self) -> None:
        """CSV and ERPNext export routes should stay removed from the workflow."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            for fmt in ("csv", "erpnext"):
                with self.assertRaisesRegex(ValueError, f"Unsupported export format: {fmt}"):
                    workflow.export_invoice(invoice_id, fmt)

    def test_workflow_rejects_unapproved_invoice_for_tally_posting(self) -> None:
        """Only approved or already posted invoices can be posted to Tally."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine, status=InvoiceStatus.PENDING_REVIEW)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with self.assertRaises(ValueError):
                workflow.post_invoice_to_tally(invoice_id)

    def test_clean_item_description(self) -> None:
        """clean_item_description helper should strip the redundant item name prefix."""
        from desktop_app.services.parsing.invoice_normalizer import clean_item_description
        self.assertEqual(clean_item_description("Services", "Services - consulting"), "consulting")
        self.assertEqual(clean_item_description("Service", "Services Renewal"), "Services Renewal")
        self.assertEqual(clean_item_description("Services", "consulting services"), "consulting services")
        self.assertEqual(clean_item_description("Services", "Services:consulting"), "consulting")
        self.assertEqual(clean_item_description("Services", "Services"), "")
        self.assertEqual(clean_item_description("", "consulting"), "consulting")
        self.assertEqual(clean_item_description("Services", ""), "")
        self.assertEqual(clean_item_description("Services", "Services - consulting\nline 2\nline 3"), "consulting\nline 2\nline 3")

    def test_inventory_purchase_voucher_includes_cleaned_description(self) -> None:
        """Item-wise voucher XML should include description tags for non-redundant details."""
        data = self.sample_invoice_data()
        data.line_items[0].description = "Additional detailed notes"
        xml = build_inventory_purchase_voucher_xml(1, data).decode("utf-8")
        self.assertIn("<DESCRIPTION>Additional detailed notes</DESCRIPTION>", xml)
        self.assertIn("<BASICUSERDESCRIPTION.LIST TYPE=\"String\">", xml)
        self.assertIn("<BASICUSERDESCRIPTION>Additional detailed notes</BASICUSERDESCRIPTION>", xml)
        self.assertIn("<ADDLDESCRIPTION.LIST TYPE=\"String\">", xml)
        self.assertIn("<ADDLDESCRIPTION>Additional detailed notes</ADDLDESCRIPTION>", xml)

    def test_inventory_purchase_voucher_omits_description_when_empty(self) -> None:
        """Item-wise voucher XML should omit description tags entirely if description is empty or None."""
        data = self.sample_invoice_data()
        data.line_items[0].description = None
        xml = build_inventory_purchase_voucher_xml(1, data).decode("utf-8")
        self.assertNotIn("<DESCRIPTION>", xml)
        self.assertNotIn("<BASICUSERDESCRIPTION.LIST", xml)
        self.assertNotIn("<ADDLDESCRIPTION.LIST", xml)

        data.line_items[0].description = ""
        xml2 = build_inventory_purchase_voucher_xml(1, data).decode("utf-8")
        self.assertNotIn("<DESCRIPTION>", xml2)
        self.assertNotIn("<BASICUSERDESCRIPTION.LIST", xml2)
        self.assertNotIn("<ADDLDESCRIPTION.LIST", xml2)


if __name__ == "__main__":
    unittest.main()
