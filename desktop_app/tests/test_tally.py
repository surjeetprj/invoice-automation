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
from desktop_app.services.tally.client import TallyPreflight
from desktop_app.services.tally.masters import TallyMaster, build_master_import_xml, build_system_ledgers_xml, required_purchase_masters
from desktop_app.services.tally.responses import TallyResponse, parse_tally_response
from desktop_app.services.tally.vouchers import build_purchase_voucher_xml
from desktop_app.services.workflow import DesktopWorkflow


class TallyServiceTests(unittest.TestCase):
    """Exercise XML builders, response parsing, and workflow posting."""

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
                    description="Consulting Service",
                    hsn_sac="9983",
                    quantity=1,
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
