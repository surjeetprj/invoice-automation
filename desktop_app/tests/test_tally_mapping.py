from __future__ import annotations

"""Tests for SQL-backed Tally master mappings."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from desktop_app.config import InvoiceStatus
from desktop_app.db.models import Base, Invoice, TallyMasterMapping
from desktop_app.db.repository import persist_extraction
from desktop_app.domain.schemas import InvoiceData, LineItem, TaxDetail, ValidationResult
from desktop_app.services.settings import TallySettings
from desktop_app.services.tally.mapping import (
    DEFAULT_SOURCE,
    INPUT_CGST_LEDGER,
    PURCHASE_LEDGER,
    STOCK_ITEM,
    UNIT,
    VENDOR_LEDGER,
    context_rows_for_invoice,
    dynamic_mapping_rows,
    get_mapping,
    migrate_legacy_settings_mappings,
    ranked_candidates,
    save_mapping,
    save_settings_mapping,
    settings_mapping_from_db,
    tally_mapping_context,
)
from desktop_app.services.tally.masters import required_inventory_purchase_masters
from desktop_app.services.tally.vouchers import build_inventory_purchase_voucher_xml, build_purchase_voucher_xml
from desktop_app.services.workflow import DesktopWorkflow
from desktop_app.services.workflow_review import mapping_company_name


class TallyMasterMappingTests(unittest.TestCase):
    """Exercise confirmed SQL mappings and suggestion helpers."""

    def make_db(self) -> Session:
        """Return an in-memory DB session with all tables created."""
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        return Session(engine, expire_on_commit=False, future=True)

    def sample_invoice(self) -> InvoiceData:
        """Return an invoice with one stock line for mapping tests."""
        return InvoiceData(
            invoice_number="PI-1",
            date="01-05-2026",
            vendor_name="Shree Medical",
            line_items=[
                LineItem(
                    item_name="Saral IncomeTax",
                    description="Saral IncomeTax v21 SAC 9984",
                    hsn_sac="9984",
                    quantity=1,
                    unit="PCS",
                    rate=1000,
                    taxable_value=1000,
                    taxes=[TaxDetail(tax_type="CGST", tax_rate=9, taxable_amount=1000, tax_amount=90)],
                    total=1090,
                )
            ],
            total_taxable_amount=1000,
            total_cgst=90,
            total_tax_amount=90,
            total_amount=1090,
        )

    def test_active_mapping_lookup_update_and_inactive_ignore(self) -> None:
        """SQL lookup should use active exact rows and update without duplicates."""
        with self.make_db() as db:
            save_mapping(db, "ABC Enterprises", VENDOR_LEDGER, "Shree Medical", "Shree Medical Agencies")
            self.assertEqual(get_mapping(db, "ABC Enterprises", VENDOR_LEDGER, "Shree Medical"), "Shree Medical Agencies")
            save_mapping(db, "ABC Enterprises", VENDOR_LEDGER, "Shree Medical", "Shree Medical Pvt Ltd")
            rows = db.scalars(select(TallyMasterMapping)).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(get_mapping(db, "ABC Enterprises", VENDOR_LEDGER, "Shree Medical"), "Shree Medical Pvt Ltd")
            save_mapping(db, "ABC Enterprises", VENDOR_LEDGER, "Inactive", "Do Not Use", is_active="N")
            self.assertIsNone(get_mapping(db, "ABC Enterprises", VENDOR_LEDGER, "Inactive"))

    def test_settings_mapping_uses_sql_default_source(self) -> None:
        """Settings ledger/group fields should save as DEFAULT SQL mappings."""
        with self.make_db() as db:
            save_settings_mapping(
                db,
                "ABC Enterprises",
                {"purchase_ledger_name": "Local Purchase", "input_cgst_ledger_name": "Local CGST"},
            )
            values = settings_mapping_from_db(db, "ABC Enterprises")
            self.assertEqual(values["purchase_ledger_name"], "Local Purchase")
            self.assertEqual(values["input_cgst_ledger_name"], "Local CGST")
            self.assertEqual(get_mapping(db, "ABC Enterprises", PURCHASE_LEDGER, DEFAULT_SOURCE), "Local Purchase")

    def test_legacy_settings_json_migrates_company_mappings_to_sql(self) -> None:
        """Old settings.json company mappings should be copied into SQL once."""
        with TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "tally": {
                            "global": {"selected_company": "ABC Enterprises"},
                            "companies": {
                                "ABC Enterprises": {
                                    "purchase_ledger_name": "Legacy Purchase",
                                    "default_stock_group": "Legacy Stock",
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.make_db() as db:
                with patch("desktop_app.services.settings.SETTINGS_FILE", settings_path):
                    self.assertEqual(migrate_legacy_settings_mappings(db), 2)
                    self.assertEqual(migrate_legacy_settings_mappings(db), 0)
                self.assertEqual(get_mapping(db, "ABC Enterprises", PURCHASE_LEDGER, DEFAULT_SOURCE), "Legacy Purchase")

    def test_dynamic_rows_use_similarity_ranked_suggestions(self) -> None:
        """Review mappings should suggest closest Tally masters without saving them."""
        with self.make_db() as db:
            rows = dynamic_mapping_rows(
                db,
                self.sample_invoice(),
                "ABC Enterprises",
                candidates={
                    VENDOR_LEDGER: ["Other Ledger", "Shree Medical Agencies"],
                    STOCK_ITEM: ["Income Tax Software", "Saral IncomeTax"],
                    UNIT: ["NOS", "PCS"],
                },
            )
        vendor = next(row for row in rows if row["mapping_type"] == VENDOR_LEDGER)
        stock = next(row for row in rows if row["mapping_type"] == STOCK_ITEM)
        self.assertEqual(vendor["company_name"], "ABC Enterprises")
        self.assertEqual(stock["company_name"], "ABC Enterprises")
        self.assertEqual(vendor["tally_value"], "Shree Medical Agencies")
        self.assertEqual(stock["tally_value"], "Saral IncomeTax")
        self.assertGreaterEqual(vendor["match_score"], ranked_candidates("Shree Medical", ["Other Ledger"])[0]["score"])

    def test_workflow_save_settings_writes_mappings_to_sql_not_json(self) -> None:
        """DesktopWorkflow should save global settings to JSON and mapping fields to SQL."""
        engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        with TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            workflow = DesktopWorkflow()
            workflow._initialized = True
            with patch("desktop_app.services.settings.SETTINGS_FILE", settings_path):
                with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
                    result = workflow.save_settings(
                        {
                            "tally": {
                                "tally_url": "http://localhost:9100",
                                "selected_company": "ABC Enterprises",
                                "purchase_ledger_name": "SQL Purchase",
                                "default_stock_group": "SQL Stock",
                            }
                        }
                    )
            persisted = json.loads(settings_path.read_text(encoding="utf-8"))
        with Session(engine, expire_on_commit=False, future=True) as db:
            self.assertEqual(get_mapping(db, "ABC Enterprises", PURCHASE_LEDGER, DEFAULT_SOURCE), "SQL Purchase")
        self.assertEqual(result["tally"]["purchase_ledger_name"], "SQL Purchase")
        self.assertEqual(result["tally"]["default_stock_group"], "SQL Stock")
        self.assertEqual(persisted["tally"]["global"]["tally_url"], "http://localhost:9100")
        self.assertNotIn("companies", persisted["tally"])
    def test_tally_xml_uses_sql_mapping_context(self) -> None:
        """Direct Tally XML builders should honor resolved SQL mappings."""
        with self.make_db() as db:
            save_settings_mapping(db, "ABC Enterprises", {"purchase_ledger_name": "Mapped Purchase", "input_cgst_ledger_name": "Mapped CGST"})
            save_mapping(db, "ABC Enterprises", VENDOR_LEDGER, "Shree Medical", "Shree Medical Agencies")
            save_mapping(db, "ABC Enterprises", STOCK_ITEM, "Saral IncomeTax", "Saral IncomeTax Local")
            save_mapping(db, "ABC Enterprises", UNIT, "PCS", "Pcs")
            rows = context_rows_for_invoice(db, self.sample_invoice(), "ABC Enterprises")
        with tally_mapping_context(rows):
            ledger_xml = build_purchase_voucher_xml(1, self.sample_invoice()).decode("utf-8")
            item_xml = build_inventory_purchase_voucher_xml(1, self.sample_invoice()).decode("utf-8")
            masters = required_inventory_purchase_masters(self.sample_invoice())
        self.assertIn("<LEDGERNAME>Shree Medical Agencies</LEDGERNAME>", ledger_xml)
        self.assertIn("<LEDGERNAME>Mapped Purchase</LEDGERNAME>", ledger_xml)
        self.assertIn("<LEDGERNAME>Mapped CGST</LEDGERNAME>", ledger_xml)
        self.assertIn("<STOCKITEMNAME>Saral IncomeTax Local</STOCKITEMNAME>", item_xml)
        self.assertIn("<ACTUALQTY>1 Pcs</ACTUALQTY>", item_xml)
        self.assertIn("Unit Master: Pcs", [master.label for master in masters])

    def test_review_mapping_save_uses_payload_company_after_settings_switch(self) -> None:
        """Submitting review mappings should save under the company used to build the row."""
        engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        data = self.sample_invoice()
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = Invoice(filename="invoice.pdf", file_path="C:/tmp/invoice.pdf", status=InvoiceStatus.PENDING_REVIEW)
            db.add(invoice)
            db.flush()
            persist_extraction(db, invoice, data, ValidationResult(is_valid=True), "raw text")
            db.commit()
            invoice_id = invoice.id

        workflow = DesktopWorkflow()
        workflow._initialized = True
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with (
                patch("desktop_app.services.workflow.get_tally_settings", return_value=TallySettings(tally_company="Company A")),
                patch("desktop_app.services.workflow.TallyClient") as client_cls,
            ):
                client_cls.return_value.fetch_master_names.return_value = set()
                record = workflow.get_invoice(invoice_id)

            mapping_row = next(row for row in record["tally_mappings"] if row["mapping_type"] == VENDOR_LEDGER)
            self.assertEqual(mapping_row["company_name"], "Company A")
            mapping_row["tally_value"] = "Shree Medical Agencies"

            with patch("desktop_app.services.workflow_review.get_tally_settings", return_value=TallySettings(tally_company="Company B")):
                workflow.submit_review(
                    invoice_id,
                    {
                        "decision": "save_corrections",
                        "reviewer": "reviewer",
                        "corrections": {"tally_mappings": [mapping_row]},
                    },
                )

        with Session(engine, expire_on_commit=False, future=True) as db:
            self.assertEqual(get_mapping(db, "Company A", VENDOR_LEDGER, "Shree Medical"), "Shree Medical Agencies")
            self.assertIsNone(get_mapping(db, "Company B", VENDOR_LEDGER, "Shree Medical"))

    def test_approve_with_corrections_mapping_save_uses_payload_company(self) -> None:
        """Approve-with-corrections should preserve mapping company and still approve."""
        engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        data = self.sample_invoice()
        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = Invoice(filename="invoice.pdf", file_path="C:/tmp/invoice.pdf", status=InvoiceStatus.PENDING_REVIEW)
            db.add(invoice)
            db.flush()
            persist_extraction(db, invoice, data, ValidationResult(is_valid=True), "raw text")
            db.commit()
            invoice_id = invoice.id

        workflow = DesktopWorkflow()
        workflow._initialized = True
        mapping_row = {
            "mapping_type": STOCK_ITEM,
            "source_value": "Saral IncomeTax",
            "company_name": "Company A",
            "tally_value": "Saral IncomeTax Local",
            "is_active": "Y",
        }
        with patch("desktop_app.services.workflow.session_scope", side_effect=lambda: Session(engine, expire_on_commit=False, future=True)):
            with patch("desktop_app.services.workflow_review.get_tally_settings", return_value=TallySettings(tally_company="Company B")):
                workflow.submit_review(
                    invoice_id,
                    {
                        "decision": "approve_with_corrections",
                        "reviewer": "reviewer",
                        "corrections": {"tally_mappings": [mapping_row]},
                    },
                )

        with Session(engine, expire_on_commit=False, future=True) as db:
            invoice = db.get(Invoice, invoice_id)
            self.assertIsNotNone(invoice)
            assert invoice is not None
            self.assertEqual(invoice.status, InvoiceStatus.APPROVED)
            self.assertEqual(get_mapping(db, "Company A", STOCK_ITEM, "Saral IncomeTax"), "Saral IncomeTax Local")
            self.assertIsNone(get_mapping(db, "Company B", STOCK_ITEM, "Saral IncomeTax"))

    def test_review_mapping_company_context_must_be_single_and_non_empty(self) -> None:
        """Current UI mapping payloads should not mix or omit company context."""
        with self.assertRaisesRegex(ValueError, "one Tally company"):
            mapping_company_name([
                {"company_name": "Company A", "mapping_type": VENDOR_LEDGER},
                {"company_name": "Company B", "mapping_type": STOCK_ITEM},
            ])
        with self.assertRaisesRegex(ValueError, "include a Tally company"):
            mapping_company_name([{"company_name": "", "mapping_type": VENDOR_LEDGER}])


if __name__ == "__main__":
    unittest.main()
