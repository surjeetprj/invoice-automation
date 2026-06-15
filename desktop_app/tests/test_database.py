from __future__ import annotations

"""Regression tests for normalized database persistence helpers."""

import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from desktop_app.config import INPUT_CESS_LEDGER_NAME, InvoiceStatus
from desktop_app.db.migrations import LEGACY_EXTRACTED_DATA_WARNING, apply_startup_migrations
from desktop_app.db.models import (
    AuditLog,
    Base,
    Invoice,
    InvoiceExtraction,
    InvoiceLineItem,
    InvoiceLineTax,
    InvoiceTaxBreakup,
    InvoiceValidationIssue,
)
from desktop_app.db.repository import (
    invoice_data_from_invoice,
    persist_extraction,
    raw_markdown_from_invoice,
    validation_from_invoice,
)
from desktop_app.domain.schemas import InvoiceData, LineItem, TaxDetail, ValidationResult
from desktop_app.domain.validation import validate_invoice
from desktop_app.services.exports.exporters import export_invoice_tally
from desktop_app.services.workflow import DesktopWorkflow
from desktop_app.ui.widgets.line_items_table import build_line_item_taxes, flatten_line_item_taxes


class DatabasePersistenceTests(unittest.TestCase):
    """Exercise normalized invoice persistence without touching runtime SQLite."""

    def make_session(self) -> Session:
        """Create an isolated in-memory database session."""
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        return Session(engine, expire_on_commit=False, future=True)

    def make_engine(self) -> Engine:
        """Create an isolated in-memory SQLite engine."""
        return create_engine("sqlite:///:memory:", future=True)

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

    def test_approve_with_corrections_preserves_nested_line_taxes_for_validation_and_export(self) -> None:
        """Review-table corrections should not drop nested tax rows hidden from the grid."""
        engine = self.make_engine()
        Base.metadata.create_all(engine)
        data = InvoiceData(
            invoice_number="INV-CESS-1",
            date="01-05-2026",
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
                    taxes=[
                        TaxDetail(tax_type="IGST", tax_rate=18, taxable_amount=1000, tax_amount=180),
                        TaxDetail(tax_type="CESS", tax_rate=1, taxable_amount=1000, tax_amount=10),
                    ],
                    cess_amount=10,
                    total=1190,
                )
            ],
            tax_breakup=[
                TaxDetail(tax_type="IGST", tax_rate=18, taxable_amount=1000, tax_amount=180),
                TaxDetail(tax_type="CESS", tax_rate=1, taxable_amount=1000, tax_amount=10),
            ],
            total_taxable_amount=1000,
            total_igst=180,
            total_cess=10,
            total_tax_amount=190,
            total_amount=1190,
        )
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = Invoice(filename="invoice.pdf", file_path="C:/tmp/invoice.pdf", status=InvoiceStatus.PENDING_REVIEW)
            db.add(invoice)
            db.flush()
            persist_extraction(db, invoice, data, validate_invoice(data), "raw text")
            db.commit()
            invoice_id = invoice.id

        original_line = data.line_items[0].model_dump(mode="json")
        edited_values = flatten_line_item_taxes(original_line)
        edited_values["description"] = "Corrected Service"
        corrected_line = build_line_item_taxes(edited_values, original_item=original_line)
        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            workflow.submit_review(
                invoice_id,
                {
                    "decision": "approve_with_corrections",
                    "reviewer": "reviewer",
                    "corrections": {"line_items": [corrected_line]},
                },
            )

        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            loaded = invoice_data_from_invoice(invoice)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(invoice.status, InvoiceStatus.APPROVED)
            self.assertEqual(loaded.line_items[0].description, "Corrected Service")
            self.assertEqual([tax.tax_type for tax in loaded.line_items[0].taxes], ["IGST", "CESS"])
            self.assertEqual(loaded.line_items[0].taxes[1].tax_amount, 10)
            self.assertEqual([tax.tax_type for tax in loaded.tax_breakup], ["IGST", "CESS"])

            validation = validate_invoice(loaded, raw_markdown_from_invoice(invoice))
            self.assertTrue(validation.is_valid)
            self.assertFalse(any("Tax amount mismatch" in warning for warning in validation.warnings))
            tally_xml, filename = export_invoice_tally(invoice_id, loaded)
            self.assertTrue(filename.endswith("_tally.xml"))
            xml = tally_xml.decode("utf-8")
            self.assertIn("<LEDGERNAME>Input IGST</LEDGERNAME>", xml)
            self.assertIn(f"<LEDGERNAME>{INPUT_CESS_LEDGER_NAME}</LEDGERNAME>", xml)

    def create_legacy_schema(
        self,
        engine: Engine,
        *,
        include_raw: bool = True,
        include_validation: bool = True,
        include_legacy_summary: bool = True,
    ) -> None:
        """Create a pre-normalization invoices table for migration tests."""
        invoice_columns = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "filename VARCHAR(255) NOT NULL",
            "file_path TEXT NOT NULL",
            "status VARCHAR(50) NOT NULL",
        ]
        if include_raw:
            invoice_columns.append("raw_markdown TEXT")
        invoice_columns.append("extracted_data TEXT")
        if include_validation:
            invoice_columns.append("validation_result TEXT")
        if include_legacy_summary:
            invoice_columns.extend(
                [
                    "invoice_number_extracted VARCHAR(100)",
                    "vendor_gstin VARCHAR(15)",
                    "supply_type VARCHAR(20)",
                    "confidence_score FLOAT",
                    "processing_time_ms INTEGER",
                    "reviewed_by VARCHAR(100)",
                    "reviewed_at DATETIME",
                    "rejection_reason TEXT",
                    "created_at DATETIME",
                    "updated_at DATETIME",
                ]
            )
        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE invoices ({', '.join(invoice_columns)})"))
            connection.execute(
                text(
                    """
                    CREATE TABLE audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        invoice_id INTEGER NOT NULL,
                        user VARCHAR(100),
                        action TEXT NOT NULL,
                        reason TEXT,
                        timestamp DATETIME
                    )
                    """
                )
            )

    def test_startup_migration_backfills_legacy_json_without_touching_summary_or_audit(self) -> None:
        """Legacy JSON columns should backfill normalized rows without summary churn."""
        engine = self.make_engine()
        self.create_legacy_schema(engine)
        data = self.sample_invoice_data()
        validation = ValidationResult(is_valid=False, errors=["Bad total"], warnings=["Check GSTIN"])

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO invoices (
                        id, filename, file_path, status, raw_markdown, extracted_data,
                        validation_result, invoice_number_extracted, vendor_gstin,
                        supply_type, confidence_score, processing_time_ms, reviewed_by,
                        reviewed_at, rejection_reason, created_at, updated_at
                    )
                    VALUES (
                        1, 'legacy.pdf', 'C:/tmp/legacy.pdf', 'Approved', :raw_markdown,
                        :extracted_data, :validation_result, 'SUMMARY-ONLY',
                        '27SUMMARYGSTIN', 'SUMMARY_TYPE', 0.12, 777,
                        'reviewer', '2026-05-02 10:30:00', 'kept',
                        '2026-05-01 09:00:00', '2026-05-03 11:00:00'
                    )
                    """
                ),
                {
                    "raw_markdown": "legacy raw markdown",
                    "extracted_data": json.dumps(data.model_dump(mode="json")),
                    "validation_result": json.dumps(validation.model_dump(mode="json")),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO audit_logs (id, invoice_id, user, action, reason, timestamp)
                    VALUES (1, 1, 'system', 'Legacy audit', 'kept', '2026-05-01 09:01:00')
                    """
                )
            )

        apply_startup_migrations(engine)

        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, 1)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            loaded = invoice_data_from_invoice(invoice)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.invoice_number, "INV-1")
            self.assertEqual(loaded.line_items[0].description, "Service")
            self.assertEqual(loaded.line_items[0].taxes[0].tax_amount, 180)
            self.assertEqual(loaded.tax_breakup[0].tax_type, "IGST")
            self.assertEqual(raw_markdown_from_invoice(invoice), "legacy raw markdown")
            self.assertEqual(invoice.extraction.document_kind, "DIGITAL_PDF")
            self.assertEqual(invoice.extraction.mime_type, "application/pdf")

            migrated_validation = validation_from_invoice(invoice)
            self.assertEqual(migrated_validation.errors, ["Bad total"])
            self.assertEqual(migrated_validation.warnings, ["Check GSTIN"])

            self.assertEqual(invoice.status, "Approved")
            self.assertEqual(invoice.invoice_number_extracted, "SUMMARY-ONLY")
            self.assertEqual(invoice.vendor_gstin, "27SUMMARYGSTIN")
            self.assertEqual(invoice.supply_type, "SUMMARY_TYPE")
            self.assertEqual(invoice.confidence_score, 0.12)
            self.assertEqual(invoice.processing_time_ms, 777)
            self.assertEqual(invoice.reviewed_by, "reviewer")
            self.assertEqual(invoice.rejection_reason, "kept")
            self.assertIsNone(invoice.file_hash)
            self.assertIsNone(invoice.invoice_date_extracted)
            self.assertIsNone(invoice.total_amount_extracted)

            audit_logs = db.scalars(select(AuditLog).where(AuditLog.invoice_id == 1)).all()
            self.assertEqual(len(audit_logs), 1)
            self.assertEqual(audit_logs[0].action, "Legacy audit")
            self.assertEqual(audit_logs[0].reason, "kept")

            self.assertEqual(db.scalar(select(func.count(InvoiceExtraction.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceLineItem.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceLineTax.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceTaxBreakup.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceValidationIssue.id))), 2)

        apply_startup_migrations(engine)

        with Session(engine, expire_on_commit=False, future=True) as db:
            self.assertEqual(db.scalar(select(func.count(InvoiceExtraction.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceLineItem.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceLineTax.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceTaxBreakup.id))), 1)
            self.assertEqual(db.scalar(select(func.count(InvoiceValidationIssue.id))), 2)
            self.assertEqual(db.scalar(select(func.count(AuditLog.id))), 1)

    def test_startup_migration_handles_partial_legacy_schema_with_only_extracted_data(self) -> None:
        """A minimal legacy table should gain ORM columns and migrate extraction JSON."""
        engine = self.make_engine()
        self.create_legacy_schema(engine, include_raw=False, include_validation=False, include_legacy_summary=False)

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO invoices (id, filename, file_path, status, extracted_data)
                    VALUES (1, 'scan.png', 'C:/tmp/scan.png', 'Pending_Review', :extracted_data)
                    """
                ),
                {"extracted_data": json.dumps(self.sample_invoice_data().model_dump(mode="json"))},
            )

        apply_startup_migrations(engine)

        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, 1)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            loaded = invoice_data_from_invoice(invoice)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.invoice_number, "INV-1")
            self.assertIsNone(raw_markdown_from_invoice(invoice))
            self.assertIsNone(invoice.extraction.document_kind)
            self.assertIsNone(invoice.extraction.mime_type)
            self.assertEqual(invoice.status, "Pending_Review")
            self.assertEqual(db.scalar(select(func.count(InvoiceValidationIssue.id))), 0)

    def test_startup_migration_preserves_raw_markdown_when_legacy_json_is_malformed(self) -> None:
        """Malformed extracted_data should not block startup or lose raw text."""
        engine = self.make_engine()
        self.create_legacy_schema(engine, include_legacy_summary=False)

        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO invoices (
                        id, filename, file_path, status, raw_markdown, extracted_data, validation_result
                    )
                    VALUES (
                        1, 'broken.pdf', 'C:/tmp/broken.pdf', 'Pending_Review',
                        'raw text survives', '{bad-json', '{also-bad'
                    )
                    """
                )
            )

        apply_startup_migrations(engine)

        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, 1)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            loaded = invoice_data_from_invoice(invoice)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertIsNone(loaded.invoice_number)
            self.assertEqual(raw_markdown_from_invoice(invoice), "raw text survives")
            self.assertEqual(invoice.extraction.document_kind, "DIGITAL_PDF")
            self.assertEqual(invoice.extraction.mime_type, "application/pdf")

            migrated_validation = validation_from_invoice(invoice)
            self.assertEqual(migrated_validation.errors, [])
            self.assertEqual(migrated_validation.warnings, [LEGACY_EXTRACTED_DATA_WARNING])
            self.assertEqual(migrated_validation.issues[0].field, "Legacy Migration")


if __name__ == "__main__":
    unittest.main()
