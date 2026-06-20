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
from desktop_app.services.tally.client import TallyPreflight, annotate_tally_response, build_tally_identity_xml, build_tally_license_info_xml, merge_tally_responses, parse_tally_serial_number
from desktop_app.services.tally.masters import (
    build_inventory_stock_items_xml,
    TallyMaster,
    build_master_import_xml,
    build_system_ledgers_xml,
    required_inventory_purchase_masters,
    required_purchase_masters,
)
from desktop_app.services.tally.responses import TallyResponse, parse_tally_response
from desktop_app.services.tally.vouchers import build_inventory_purchase_voucher_xml, build_purchase_voucher_xml
from desktop_app.services.workflow import DesktopWorkflow


class TallyServiceTests(unittest.TestCase):
    """Exercise XML builders, response parsing, and workflow posting."""

    def setUp(self) -> None:
        """Keep existing posting tests focused on Tally behavior, not licensing."""
        self.license_patch = patch("desktop_app.services.workflow.assert_tally_serial_allowed")
        self.license_check = self.license_patch.start()

    def tearDown(self) -> None:
        self.license_patch.stop()

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

    def test_tally_response_parses_success_failure_and_malformed_xml(self) -> None:
        """Tally responses should normalize success and failure cases."""
        success = parse_tally_response("<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED><ERRORS>0</ERRORS></RESPONSE>")
        self.assertTrue(success.success)
        self.assertEqual(success.created, 1)

        failure = parse_tally_response("<RESPONSE><CREATED>0</CREATED><ERRORS>1</ERRORS><LINEERROR>Missing ledger</LINEERROR></RESPONSE>")
        self.assertFalse(failure.success)
        self.assertEqual(failure.errors, 1)
        self.assertIn("Missing ledger", failure.messages)

        malformed = parse_tally_response("not xml")
        self.assertFalse(malformed.success)
        self.assertEqual(malformed.errors, 1)

    def test_tally_client_parses_serial_number_from_identity_response(self) -> None:
        """Tally identity XML responses should expose license serial fields."""
        xml = """
        <ENVELOPE><BODY><DATA><COLLECTION>
          <COMPANY><NAME>Demo</NAME><LICENSESERIALNUMBER>TALLY-12345</LICENSESERIALNUMBER></COMPANY>
        </COLLECTION></DATA></BODY></ENVELOPE>
        """
        self.assertEqual(parse_tally_serial_number(xml), "TALLY-12345")
        self.assertEqual(parse_tally_serial_number("<GETSERIALFIELD>Serial Number TALLY-24680</GETSERIALFIELD>"), "TALLY-24680")
        self.assertEqual(parse_tally_serial_number("<COMPANY><LICENSESERIALNUMBER>Serial Number TALLY-67890</LICENSESERIALNUMBER></COMPANY>"), "TALLY-67890")
        self.assertIn(b"InvoiceAITallyIdentity", build_tally_identity_xml())
        license_xml = build_tally_license_info_xml("Runtime Company")
        self.assertIn(b"SVCURRENTCOMPANY", license_xml)
        self.assertIn(b"Runtime Company", license_xml)
        self.assertIn(b"$$LicenseInfo:SerialNumber", license_xml)
        self.assertIn(b"<TYPE>DATA</TYPE>", license_xml)
        self.assertIn(b"InvoiceAILicenseInfoReport", license_xml)

    def test_tally_client_uses_license_info_probe_first(self) -> None:
        """The TDL LicenseInfo report probe should be preferred for connected serial verification."""
        client = TallyClient(serial_number="")
        with self.assertLogs("desktop_app.services.tally.client", level="INFO") as logs:
            with patch.object(client, "post_xml", return_value="<GETSERIALFIELD>TALLY-12345</GETSERIALFIELD>") as post_xml:
                self.assertEqual(client.fetch_tally_serial_number(), "TALLY-12345")

        self.assertEqual(post_xml.call_count, 1)
        self.assertIn(b"$$LicenseInfo:SerialNumber", post_xml.call_args.args[0])
        self.assertIn("Tally serial verified using LicenseInfo TDL report probe", "\n".join(logs.output))

    def test_tally_client_falls_back_to_identity_probe(self) -> None:
        """Company collection identity probe should be used when LicenseInfo returns no serial."""
        client = TallyClient(serial_number="")
        with self.assertLogs("desktop_app.services.tally.client", level="INFO") as logs:
            with patch.object(
                client,
                "post_xml",
                side_effect=[
                    "<ENVELOPE><BODY><DATA /></BODY></ENVELOPE>",
                    "<ENVELOPE><COMPANY><LICENSESERIALNUMBER>TALLY-67890</LICENSESERIALNUMBER></COMPANY></ENVELOPE>",
                ],
            ) as post_xml:
                self.assertEqual(client.fetch_tally_serial_number(), "TALLY-67890")

        self.assertEqual(post_xml.call_count, 2)
        self.assertIn(b"$$LicenseInfo:SerialNumber", post_xml.call_args_list[0].args[0])
        self.assertIn(b"InvoiceAITallyIdentity", post_xml.call_args_list[1].args[0])
        self.assertIn("Tally serial verified using Company collection identity probe", "\n".join(logs.output))

    def test_tally_client_uses_configured_serial_when_tally_does_not_expose_one(self) -> None:
        """Hidden .env serial fallback should remain available after both HTTP probes fail."""
        client = TallyClient(serial_number="TALLY-12345")
        with self.assertLogs("desktop_app.services.tally.client", level="WARNING") as logs:
            with patch.object(
                client,
                "post_xml",
                side_effect=[
                    "<ENVELOPE><COMPANY><NAME>Demo</NAME></COMPANY></ENVELOPE>",
                    "<ENVELOPE><COMPANY><NAME>Demo</NAME></COMPANY></ENVELOPE>",
                ],
            ) as post_xml:
                self.assertEqual(client.fetch_tally_serial_number(), "TALLY-12345")

        self.assertEqual(post_xml.call_count, 2)
        self.assertIn("support-only .env fallback", "\n".join(logs.output))

    def test_tally_client_fails_closed_when_serial_missing(self) -> None:
        """Missing serial fields should block TallyPrime export when no fallback is configured."""
        client = TallyClient(serial_number="")
        with patch.object(
            client,
            "post_xml",
            side_effect=[
                "<ENVELOPE><COMPANY><NAME>Demo</NAME></COMPANY></ENVELOPE>",
                "<ENVELOPE><COMPANY><NAME>Demo</NAME></COMPANY></ENVELOPE>",
            ],
        ):
            with self.assertRaisesRegex(ConnectionError, "Could not verify TallyPrime serial number"):
                client.fetch_tally_serial_number()

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
        </DATA></BODY></ENVELOPE>
        """
        from desktop_app.services.tally.client import parse_master_names

        names = parse_master_names(xml)
        self.assertIn("Vendor Pvt Ltd", names)
        self.assertIn("Purchase Account", names)

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

    def test_workflow_posts_approved_invoice_and_marks_posted(self) -> None:
        """Successful Tally posting should mark the invoice Posted and audit it."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.check_connection.return_value = None
                client.preflight_purchase_invoice.return_value = TallyPreflight((), ())
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.post_purchase_voucher.return_value = TallyResponse(success=True, created=1)
                result = workflow.post_invoice_to_tally(invoice_id)

        self.assertTrue(result["success"])
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.POSTED)
            logs = db.scalars(select(AuditLog).where(AuditLog.invoice_id == invoice_id)).all()
            self.assertTrue(any("Pushed to TallyPrime" in log.action for log in logs))

    def test_workflow_posts_itemwise_invoice_and_marks_posted(self) -> None:
        """Successful inventory Tally posting should mark the invoice Posted and audit it."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.preflight_inventory_purchase_invoice.return_value = TallyPreflight((), ())
                client.sync_vendor_master.return_value = TallyResponse(success=True, altered=1)
                client.sync_system_ledgers.return_value = TallyResponse(success=True, altered=4)
                client.sync_inventory_item_masters.return_value = TallyResponse(success=True, altered=1)
                client.post_inventory_purchase_voucher.return_value = TallyResponse(success=True, created=1)
                result = workflow.post_invoice_items_to_tally(invoice_id)

        self.assertTrue(result["success"])
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.POSTED)
            logs = db.scalars(select(AuditLog).where(AuditLog.invoice_id == invoice_id)).all()
            self.assertTrue(any("item-wise to TallyPrime" in log.action for log in logs))

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
                client.preflight_inventory_purchase_invoice.side_effect = ValueError(
                    "Item posting requires complete reviewed line items.\nLine 1: quantity must be greater than 0"
                )
                with self.assertRaises(ValueError):
                    workflow.post_invoice_items_to_tally(invoice_id)
                client.post_inventory_purchase_voucher.assert_not_called()

    def test_workflow_tally_post_is_blocked_before_preflight_when_license_rejects(self) -> None:
        """Ledger-only Tally posting should fail before preflight or voucher calls."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        self.license_check.side_effect = ValueError("license blocked")
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_tally_serial_number.return_value = "BAD-SERIAL"
                with self.assertRaisesRegex(ValueError, "license blocked"):
                    workflow.post_invoice_to_tally(invoice_id)

        client.preflight_purchase_invoice.assert_not_called()
        client.sync_vendor_master.assert_not_called()
        client.sync_system_ledgers.assert_not_called()
        client.post_purchase_voucher.assert_not_called()

    def test_workflow_tally_item_post_is_blocked_before_preflight_when_license_rejects(self) -> None:
        """Item-wise Tally posting should fail before inventory preflight or posting."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        self.license_check.side_effect = ValueError("license blocked")
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_tally_serial_number.return_value = "BAD-SERIAL"
                with self.assertRaisesRegex(ValueError, "license blocked"):
                    workflow.post_invoice_items_to_tally(invoice_id)

        client.preflight_inventory_purchase_invoice.assert_not_called()
        client.sync_vendor_master.assert_not_called()
        client.sync_system_ledgers.assert_not_called()
        client.sync_inventory_item_masters.assert_not_called()
        client.post_inventory_purchase_voucher.assert_not_called()

    def test_workflow_tally_syncs_are_blocked_when_license_rejects(self) -> None:
        """Vendor and system ledger syncs should also require a matching Tally serial."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        self.license_check.side_effect = ValueError("license blocked")
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow.TallyClient") as client_cls:
                client = client_cls.return_value
                client.fetch_tally_serial_number.return_value = "BAD-SERIAL"
                with self.assertRaisesRegex(ValueError, "license blocked"):
                    workflow.sync_vendor_master_to_tally(invoice_id)
                with self.assertRaisesRegex(ValueError, "license blocked"):
                    workflow.sync_tally_system_ledgers(invoice_id)

        self.assertEqual(client.fetch_tally_serial_number.call_count, 2)
        client.sync_vendor_master.assert_not_called()
        client.sync_system_ledgers.assert_not_called()

    def test_downloadable_exports_do_not_check_tally_license(self) -> None:
        """File-based exports should remain available without direct Tally serial checks."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            for fmt in ("csv", "json", "tally"):
                content, filename = workflow.export_invoice(invoice_id, fmt)
                self.assertIsNotNone(filename)
                self.assertTrue(content)

        self.license_check.assert_not_called()

    def test_workflow_rejects_unapproved_invoice_for_tally_posting(self) -> None:
        """Only approved or already posted invoices can be posted to Tally."""
        engine = self.make_engine()
        invoice_id = self.create_invoice(engine, status=InvoiceStatus.PENDING_REVIEW)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with self.assertRaises(ValueError):
                workflow.post_invoice_to_tally(invoice_id)


if __name__ == "__main__":
    unittest.main()
