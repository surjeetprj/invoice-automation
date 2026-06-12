from __future__ import annotations

"""Regression tests for normalized database persistence helpers."""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from desktop_app.config import InvoiceStatus
from desktop_app.db.models import Base, Invoice
from desktop_app.db.repository import invoice_data_from_invoice, persist_extraction, validation_from_invoice
from desktop_app.domain.schemas import InvoiceData, LineItem, TaxDetail, ValidationResult
from desktop_app.services.workflow import DesktopWorkflow


class DatabasePersistenceTests(unittest.TestCase):
    """Exercise normalized invoice persistence without touching runtime SQLite."""

    def make_session(self) -> Session:
        """Create an isolated in-memory database session."""
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        return Session(engine, expire_on_commit=False, future=True)

    def sample_invoice_data(self) -> InvoiceData:
        """Return a complete invoice payload with line taxes and tax breakup."""
        return InvoiceData(
            invoice_number="INV-1",
            date="01-05-2026",
            due_date="10-05-2026",
            supply_type="INTER_STATE",
            vendor_name="Vendor Pvt Ltd",
            vendor_gstin="27ABCDE1234F1Z5",
            customer_name="Customer Pvt Ltd",
            customer_gstin="09AAOCS7654P3Z5",
            place_of_supply="Uttar Pradesh",
            line_items=[
                LineItem(
                    sr_no=1,
                    description="Service",
                    hsn_sac="9983",
                    quantity=1,
                    rate=1000,
                    taxable_value=1000,
                    taxes=[TaxDetail(tax_type="IGST", tax_rate=18, taxable_amount=1000, tax_amount=180)],
                    total=1000,
                )
            ],
            tax_breakup=[TaxDetail(tax_type="IGST", tax_rate=18, taxable_amount=1000, tax_amount=180)],
            total_taxable_amount=1000,
            total_igst=180,
            total_tax_amount=180,
            total_amount=1180,
            bank_name="Bank",
            account_no="123",
            ifsc="IFSC0001",
            confidence_score=0.92,
        )

    def test_invoice_data_round_trips_through_normalized_tables(self) -> None:
        """InvoiceData, line items, line taxes, and tax breakups should round-trip."""
        with self.make_session() as db:
            invoice = Invoice(filename="invoice.pdf", file_path="C:/tmp/invoice.pdf", status=InvoiceStatus.PENDING_REVIEW)
            db.add(invoice)
            db.flush()
            data = self.sample_invoice_data()
            validation = ValidationResult(is_valid=True)

            persist_extraction(db, invoice, data, validation, "raw text", document_kind="DIGITAL_PDF", mime_type="application/pdf")
            db.commit()
            db.refresh(invoice)

            loaded = invoice_data_from_invoice(invoice)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.invoice_number, "INV-1")
            self.assertEqual(loaded.line_items[0].description, "Service")
            self.assertEqual(loaded.line_items[0].taxes[0].tax_type, "IGST")
            self.assertEqual(loaded.tax_breakup[0].tax_amount, 180)
            self.assertEqual(invoice.invoice_number_extracted, "INV-1")
            self.assertEqual(invoice.total_amount_extracted, 1180)
            self.assertEqual(invoice.extraction.document_kind, "DIGITAL_PDF")
            self.assertEqual(invoice.extraction.mime_type, "application/pdf")

    def test_validation_issues_round_trip(self) -> None:
        """Validation errors and warnings should rebuild from issue rows."""
        with self.make_session() as db:
            invoice = Invoice(filename="invoice.pdf", file_path="C:/tmp/invoice.pdf", status=InvoiceStatus.PENDING_REVIEW)
            db.add(invoice)
            db.flush()
            validation = ValidationResult(
                is_valid=False,
                errors=["Bad total"],
                warnings=["Check GSTIN"],
            )

            persist_extraction(db, invoice, self.sample_invoice_data(), validation, "raw text")
            db.commit()
            db.refresh(invoice)

            loaded = validation_from_invoice(invoice)
            self.assertFalse(loaded.is_valid)
            self.assertEqual(loaded.errors, ["Bad total"])
            self.assertEqual(loaded.warnings, ["Check GSTIN"])
            self.assertEqual(len(loaded.issues), 2)

    def test_workflow_record_shape_matches_ui_contract(self) -> None:
        """DesktopWorkflow should expose the same InvoiceRecord payload shape."""
        with self.make_session() as db:
            invoice = Invoice(filename="invoice.pdf", file_path="C:/tmp/invoice.pdf", status=InvoiceStatus.PENDING_REVIEW)
            db.add(invoice)
            db.flush()
            persist_extraction(db, invoice, self.sample_invoice_data(), ValidationResult(is_valid=True), "raw text")
            db.commit()
            db.refresh(invoice)

            record = DesktopWorkflow().invoice_to_record(invoice).model_dump(mode="json")
            self.assertEqual(record["filename"], "invoice.pdf")
            self.assertEqual(record["raw_markdown"], "raw text")
            self.assertEqual(record["extracted_data"]["invoice_number"], "INV-1")
            self.assertEqual(record["validation"]["issues"], [])


if __name__ == "__main__":
    unittest.main()
