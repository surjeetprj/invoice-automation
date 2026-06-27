from __future__ import annotations

"""In-process application workflow used by the PySide6 desktop UI."""

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select

from ..config import DUPLICATE_CHECK_ENABLED, InvoiceStatus, UPLOAD_DIR
from ..db.models import AuditLog, Invoice
from ..db.repository import (
    invoice_data_from_invoice,
    persist_extraction,
    raw_markdown_from_invoice,
    validation_from_invoice,
)
from ..db.session import init_db, session_scope
from ..domain.schemas import (
    AuditLogRecord,
    DashboardStats,
    InvoiceData,
    InvoiceRecord,
    InvoiceReviewRequest,
    ReviewDecision,
    ValidationResult,
)
from .documents.document_source import DocumentKind, classify_document, validate_upload_file
from .documents.extraction import ScannedDocumentException
from .exports.exporters import export_invoice_json, export_invoice_tally
from .parsing.ai_client import AIRateLimitError
from .parsing.ai_parser import extract_invoice_source_text, parse_invoice, parse_invoice_file
from .settings import build_tally_settings, get_tally_settings, get_tally_settings_payload, save_tally_settings
from .tally import TallyClient
from .tally.mapping import (
    STOCK_ITEM,
    UNIT,
    VENDOR_LEDGER,
    all_company_mappings,
    context_rows_for_invoice,
    context_rows_for_settings,
    dynamic_mapping_rows,
    migrate_legacy_settings_mappings,
    save_settings_mapping,
    settings_mapping_from_db,
    tally_mapping_context,
)
from .workflow_pipeline import document_kind_label, sha256_file
from .workflow_review import apply_review_decision, ensure_review_allowed
from .workflow_tally import assert_tally_company_selected as verify_tally_company_selected
from ..domain.validation import calculate_confidence_score, validate_invoice

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[dict[str, str]], None]


class DesktopWorkflow:
    """Facade for all invoice operations used by the desktop UI."""

    def initialize(self) -> None:
        """Initialize the local desktop database."""
        if getattr(self, "_initialized", False):
            return
        logger.info("Initializing desktop database")
        init_db()
        with session_scope() as db:
            migrated = migrate_legacy_settings_mappings(db)
            if migrated:
                logger.info("Migrated %s legacy Tally settings mappings into SQL", migrated)
                db.commit()
        self._initialized = True

    def get_settings(self) -> dict[str, Any]:
        """Return runtime-editable desktop settings for the UI."""
        self.initialize()
        payload = get_tally_settings_payload()
        with session_scope() as db:
            selected_company = str(payload.get("selected_company") or payload.get("tally_company") or "").strip()
            payload.update(settings_mapping_from_db(db, selected_company))
            payload["company_mappings"] = all_company_mappings(db)
        return {"tally": payload}

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist runtime-editable desktop settings."""
        self.initialize()
        tally_payload = payload.get("tally", payload) if isinstance(payload, dict) else {}
        tally_payload = tally_payload if isinstance(tally_payload, dict) else {}
        save_tally_settings(tally_payload)
        company_name = str(tally_payload.get("selected_company") or tally_payload.get("tally_company") or "").strip()
        with session_scope() as db:
            if company_name:
                saved = save_settings_mapping(db, company_name, tally_payload)
                if saved:
                    logger.info("Saved %s Tally mapping setting(s) for company %s", saved, company_name)
                db.commit()
        return self.get_settings()
    def list_tally_companies(self) -> list[str]:
        """Return available company names from the local TallyPrime HTTP endpoint."""
        self.initialize()
        logger.info("Listing TallyPrime companies")
        return sorted(TallyClient().fetch_company_names(), key=str.casefold)

    def list_tally_ledgers(self, company: str | None = None) -> list[str]:
        """Return ledger names from TallyPrime for the selected company."""
        self.initialize()
        selected_company = (company or get_tally_settings().tally_company or "").strip()
        logger.info("Listing TallyPrime ledgers for company: %s", selected_company or "<default>")
        return sorted(TallyClient().fetch_master_names("InvoiceAISettingsLedgers", "Ledger", company=selected_company), key=str.casefold)

    def list_tally_stock_groups(self, company: str | None = None) -> list[str]:
        """Return stock group names from TallyPrime for the selected company."""
        self.initialize()
        selected_company = (company or get_tally_settings().tally_company or "").strip()
        logger.info("Listing TallyPrime stock groups for company: %s", selected_company or "<default>")
        return sorted(TallyClient().fetch_master_names("InvoiceAISettingsStockGroups", "Stock Group", company=selected_company), key=str.casefold)

    def list_tally_options(self, company: str | None = None) -> dict[str, Any]:
        """Return categorized and filtered TallyPrime choices for settings dropdowns."""
        self.initialize()
        selected_company = (company or get_tally_settings().tally_company or "").strip()
        logger.info("Listing TallyPrime options for company: %s", selected_company or "<default>")
        
        client = TallyClient()
        raw_groups = client.fetch_master_details("InvoiceAISettingsGroups", "Group", company=selected_company)
        raw_ledgers = client.fetch_master_details("InvoiceAISettingsLedgersWithParent", "Ledger", company=selected_company)
        stock_groups = sorted(client.fetch_master_names("InvoiceAISettingsStockGroups", "Stock Group", company=selected_company), key=str.casefold)
        
        parent_map = {g["name"]: g["parent"] for g in raw_groups}
        
        def is_descendant(current_group: str, target_parent: str) -> bool:
            visited = set()
            while current_group:
                if current_group.lower().strip() == target_parent.lower().strip():
                    return True
                if current_group in visited:
                    break
                visited.add(current_group)
                current_group = parent_map.get(current_group, "")
            return False
            
        filtered_groups = [
            g["name"] for g in raw_groups 
            if is_descendant(g["name"], "Sundry Creditors")
        ]
        
        purchase_ledgers = [
            l["name"] for l in raw_ledgers 
            if is_descendant(l["parent"], "Purchase Accounts")
        ]
        
        duty_ledgers = [
            l["name"] for l in raw_ledgers 
            if is_descendant(l["parent"], "Duties & Taxes")
        ]
        
        if not filtered_groups:
            filtered_groups = [g["name"] for g in raw_groups]
        if not purchase_ledgers:
            purchase_ledgers = [l["name"] for l in raw_ledgers]
        if not duty_ledgers:
            duty_ledgers = [l["name"] for l in raw_ledgers]
            
        return {
            "groups": sorted(list(set(filtered_groups)), key=str.casefold),
            "purchase_ledgers": sorted(list(set(purchase_ledgers)), key=str.casefold),
            "duty_ledgers": sorted(list(set(duty_ledgers)), key=str.casefold),
            "stock_groups": stock_groups,
        }

    def test_tally_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Test TallyPrime reachability and serial detection for unsaved settings."""
        self.initialize()
        tally_payload = payload.get("tally", payload) if isinstance(payload, dict) else {}
        settings = build_tally_settings(tally_payload if isinstance(tally_payload, dict) else {})
        client = TallyClient(
            url=settings.tally_url,
            timeout=settings.tally_timeout_seconds,
        )
        companies = sorted(client.fetch_company_names(), key=str.casefold)
        serial = client.fetch_tally_serial_number()
        return {
            "success": True,
            "message": "TallyPrime connection verified.",
            "serial_number": serial,
            "companies": companies,
        }

    def health(self) -> dict[str, str]:
        """Return a lightweight readiness payload."""
        self.initialize()
        return {"status": "healthy", "service": "Invoice AI Desktop"}

    def stats(self) -> dict[str, Any]:
        """Calculate dashboard statistics from the local database."""
        self.initialize()
        logger.info("Loading dashboard stats")
        with session_scope() as db:
            total = db.scalar(select(func.count(Invoice.id))) or 0
            avg_time = db.scalar(select(func.avg(Invoice.processing_time_ms)))
            avg_conf = db.scalar(select(func.avg(Invoice.confidence_score)))
            by_status = {
                status: db.scalar(select(func.count(Invoice.id)).where(Invoice.status == status)) or 0
                for status in InvoiceStatus.ALL
            }
            by_status = {key: value for key, value in by_status.items() if value > 0}
            approved = db.scalar(select(func.count(Invoice.id)).where(Invoice.status.in_([InvoiceStatus.APPROVED, InvoiceStatus.POSTED]))) or 0
            stats = DashboardStats(
                total_invoices=total,
                by_status=by_status,
                status_distribution=by_status,
                avg_processing_time_ms=round(float(avg_time), 1) if avg_time else None,
                avg_confidence_score=round(float(avg_conf), 2) if avg_conf else None,
                total_pending_review=by_status.get(InvoiceStatus.PENDING_REVIEW, 0),
                total_approved=approved,
                total_rejected=by_status.get(InvoiceStatus.REJECTED, 0),
            )
            return stats.model_dump(mode="json")

    def list_invoices(self, skip: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return paginated invoice records for the invoice list page."""
        self.initialize()
        logger.info("Listing invoices skip=%s limit=%s", skip, limit)
        with session_scope() as db:
            total = db.scalar(select(func.count(Invoice.id))) or 0
            rows = db.scalars(
                select(Invoice).order_by(Invoice.created_at.desc()).offset(skip).limit(limit)
            ).all()
            return {"total": total, "invoices": [self.invoice_to_record(row).model_dump(mode="json") for row in rows]}

    def get_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Return one invoice record by ID."""
        self.initialize()
        logger.info("Loading invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            return self.invoice_to_record(invoice, db=db, include_tally_mappings=True).model_dump(mode="json")

    def get_document_path(self, invoice_id: int) -> Path:
        """Return the local source document path for an invoice."""
        self.initialize()
        logger.info("Resolving document path for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            path = Path(invoice.file_path)
            if not path.exists():
                raise FileNotFoundError(f"Invoice file not found: {path}")
            return path

    def get_pdf_path(self, invoice_id: int) -> Path:
        """Return the local source document path for older UI callers."""
        return self.get_document_path(invoice_id)

    def upload_invoice(self, source_path: str | Path, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
        """Copy, process, persist, and return a newly uploaded invoice."""
        self.initialize()
        source = Path(source_path)
        logger.info("Upload requested: %s", source)
        self.progress(progress_callback, "Checking file type and size...")
        validate_upload_file(source)
        self.progress(progress_callback, "Computing invoice fingerprint...")
        file_hash = sha256_file(source)
        self.progress(progress_callback, "Copying invoice into local workspace...")
        target = self.unique_upload_path(source.name)
        shutil.copy2(source, target)

        with session_scope() as db:
            invoice = Invoice(filename=source.name, file_path=str(target), file_hash=file_hash, status=InvoiceStatus.NEW)
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            self.progress(progress_callback, f"Created local invoice record #{invoice.id}.")
            self.log(db, invoice.id, "Invoice uploaded", reason=f"File: {source.name}")
            if DUPLICATE_CHECK_ENABLED:
                self.progress(progress_callback, "Checking for duplicate invoice content...")
                duplicate = db.scalar(
                    select(Invoice.id).where(Invoice.file_hash == file_hash, Invoice.id != invoice.id)
                )
                if duplicate:
                    self.progress(progress_callback, f"Duplicate content detected. It matches invoice #{duplicate}.", level="warning")
                    self.log(db, invoice.id, f"WARNING: Duplicate file content detected (matches invoice #{duplicate})")
            try:
                self.run_pipeline(db, invoice, target, progress_callback=progress_callback)
            except ScannedDocumentException as exc:
                invoice.status = InvoiceStatus.PENDING_REVIEW
                db.commit()
                self.progress(progress_callback, f"Document route warning: {exc}", level="warning")
                self.log(db, invoice.id, f"Pipeline extraction route error: {exc}")
            except Exception as exc:
                logger.exception("Pipeline error for invoice %s", invoice.id)
                invoice.status = InvoiceStatus.PENDING_REVIEW
                db.commit()
                self.progress(progress_callback, f"Processing failed: {exc}", level="error")
                self.log(db, invoice.id, f"Pipeline error: {exc}")
            db.refresh(invoice)
            return self.invoice_to_record(invoice, db=db, include_tally_mappings=True).model_dump(mode="json")

    def reprocess_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Run the extraction pipeline again for an existing invoice."""
        self.initialize()
        logger.info("Reprocess requested for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            path = Path(invoice.file_path)
            if not path.exists():
                raise FileNotFoundError(f"Original file not found: {path}")
            invoice.reprocess_count = (invoice.reprocess_count or 0) + 1
            self.log(db, invoice.id, "Reprocessing triggered", user="human")
            self.run_pipeline(db, invoice, path)
            db.refresh(invoice)
            return self.invoice_to_record(invoice, db=db, include_tally_mappings=True).model_dump(mode="json")

    def submit_review(self, invoice_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply a reviewer decision and return the updated invoice."""
        self.initialize()
        review = InvoiceReviewRequest(**payload)
        logger.info("Review submitted for invoice #%s: %s", invoice_id, review.decision.value)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            ensure_review_allowed(invoice, review.decision)
            now = datetime.now(timezone.utc)
            apply_review_decision(
                db,
                invoice,
                review,
                now,
                lambda invoice_id, action, reason, user: self.log(db, invoice_id, action, reason=reason, user=user),
            )
            db.commit()
            db.refresh(invoice)
            return self.invoice_to_record(invoice, db=db, include_tally_mappings=True).model_dump(mode="json")

    def audit_log(self, invoice_id: int) -> list[dict[str, Any]]:
        """Return audit timeline rows for an invoice."""
        self.initialize()
        logger.info("Loading audit log for invoice #%s", invoice_id)
        with session_scope() as db:
            self.require_invoice(db, invoice_id)
            rows = db.scalars(
                select(AuditLog).where(AuditLog.invoice_id == invoice_id).order_by(AuditLog.timestamp.asc())
            ).all()
            records = [
                AuditLogRecord(
                    id=row.id,
                    invoice_id=row.invoice_id,
                    user=row.user,
                    action=row.action,
                    reason=row.reason,
                    timestamp=row.timestamp,
                ).model_dump(mode="json")
                for row in rows
            ]
            logger.info("Loaded %d audit log rows for invoice #%s", len(records), invoice_id)
            return records

    def export_invoice(self, invoice_id: int, fmt: str) -> tuple[bytes | dict[str, Any], str | None]:
        """Export an approved invoice in the requested format."""
        self.initialize()
        logger.info("Export requested for invoice #%s as %s", invoice_id, fmt)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            if invoice.status not in {InvoiceStatus.APPROVED, InvoiceStatus.POSTED}:
                raise ValueError("Export only allowed from Approved or Posted status.")
            data = invoice_data_from_invoice(invoice)
            if data is None:
                raise ValueError("No extracted invoice data is available for export.")
            if fmt == "json":
                content, filename = export_invoice_json(invoice_id, data)
            elif fmt == "tally":
                content, filename = export_invoice_tally(invoice_id, data)
            else:
                raise ValueError(f"Unsupported export format: {fmt}")
            return content, filename

    def tally_preflight(self, invoice_id: int) -> dict[str, Any]:
        """Return missing TallyPrime masters for an approved invoice."""
        self.initialize()
        logger.info("Tally preflight requested for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            if invoice.status not in {InvoiceStatus.APPROVED, InvoiceStatus.POSTED}:
                raise ValueError("Tally posting only allowed from Approved or Posted status.")
            data = invoice_data_from_invoice(invoice)
            if data is None:
                raise ValueError("No extracted invoice data is available for Tally posting.")
            client = TallyClient()
            self.assert_tally_company_selected(client)
            client.check_connection()
            with tally_mapping_context(self.tally_mapping_rows_for_posting(db, data)):
                preflight = client.preflight_purchase_invoice(data)
            return {
                "missing_masters": preflight.missing_labels(),
                "has_missing": preflight.has_missing,
            }

    def tally_inventory_preflight(self, invoice_id: int) -> dict[str, Any]:
        """Return missing stock/unit masters for an approved invoice item post."""
        self.initialize()
        logger.info("Tally inventory preflight requested for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            if invoice.status not in {InvoiceStatus.APPROVED, InvoiceStatus.POSTED}:
                raise ValueError("Tally item posting only allowed from Approved or Posted status.")
            data = invoice_data_from_invoice(invoice)
            if data is None:
                raise ValueError("No extracted invoice data is available for Tally item posting.")
            client = TallyClient()
            self.assert_tally_company_selected(client)
            with tally_mapping_context(self.tally_mapping_rows_for_posting(db, data)):
                preflight = client.preflight_inventory_purchase_invoice(data)
            return {
                "missing_masters": preflight.missing_labels(),
                "has_missing": preflight.has_missing,
            }

    def post_invoice_to_tally(self, invoice_id: int, *, create_missing_masters: bool = False) -> dict[str, Any]:
        """Post an approved invoice as a ledger-only Purchase voucher to TallyPrime.

        This path creates confirmed missing accounting masters, syncs the vendor
        ledger and purchase/GST ledgers, and posts a Purchase voucher without
        inventory item rows.
        """
        self.initialize()
        logger.info("Tally posting requested for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            if invoice.status not in {InvoiceStatus.APPROVED, InvoiceStatus.POSTED}:
                raise ValueError("Tally posting only allowed from Approved or Posted status.")
            data = invoice_data_from_invoice(invoice)
            if data is None:
                raise ValueError("No extracted invoice data is available for Tally posting.")
            client = TallyClient()
            self.assert_tally_company_selected(client)
            client.check_connection()
            rows = self.tally_mapping_rows_for_posting(db, data)
            with tally_mapping_context(rows):
                preflight = client.preflight_purchase_invoice(data)
            if preflight.has_missing:
                if not create_missing_masters:
                    return {
                        "success": False,
                        "requires_confirmation": True,
                        "missing_masters": preflight.missing_labels(),
                    }
                with tally_mapping_context(rows):
                    master_response = client.create_missing_masters(preflight.missing_masters)
                if not master_response.success:
                    raise ValueError(f"Tally master creation failed: {master_response.summary}")
                self.log(db, invoice.id, f"TallyPrime masters created: {master_response.summary}")
            with tally_mapping_context(rows):
                vendor_response = client.sync_vendor_master(data)
            if vendor_response.success:
                self.log(db, invoice.id, f"TallyPrime vendor master synced: {vendor_response.summary}")
            with tally_mapping_context(rows):
                ledgers_response = client.sync_system_ledgers()
            if ledgers_response.success:
                self.log(db, invoice.id, f"TallyPrime purchase/GST ledgers synced: {ledgers_response.summary}")
            with tally_mapping_context(rows):
                voucher_response = client.post_purchase_voucher(invoice.id, data)
            if not voucher_response.success:
                raise ValueError(f"TallyPrime voucher posting failed: {voucher_response.summary}")
            invoice.status = InvoiceStatus.POSTED
            db.commit()
            self.log(db, invoice.id, f"Pushed to TallyPrime ({voucher_response.summary}) - status set to Posted")
            return {"success": True, "message": "Invoice posted to TallyPrime.", "tally_response": voucher_response.summary}

    def post_invoice_items_to_tally(self, invoice_id: int, *, create_missing_masters: bool = False) -> dict[str, Any]:
        """Post an approved invoice as an item-wise Purchase voucher to TallyPrime.

        This path creates confirmed missing inventory masters, syncs the vendor
        ledger, purchase/GST ledgers, and stock item HSN/GST metadata, then posts
        a Purchase voucher with inventory item rows.
        """
        self.initialize()
        logger.info("Tally item posting requested for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            if invoice.status not in {InvoiceStatus.APPROVED, InvoiceStatus.POSTED}:
                raise ValueError("Tally item posting only allowed from Approved or Posted status.")
            data = invoice_data_from_invoice(invoice)
            if data is None:
                raise ValueError("No extracted invoice data is available for Tally item posting.")
            client = TallyClient()
            self.assert_tally_company_selected(client)
            rows = self.tally_mapping_rows_for_posting(db, data)
            with tally_mapping_context(rows):
                preflight = client.preflight_inventory_purchase_invoice(data)
            if preflight.has_missing:
                if not create_missing_masters:
                    return {
                        "success": False,
                        "requires_confirmation": True,
                        "missing_masters": preflight.missing_labels(),
                    }
                with tally_mapping_context(rows):
                    master_response = client.create_missing_inventory_masters(preflight.missing_masters)
                if not master_response.success:
                    raise ValueError(f"Tally inventory master creation failed: {master_response.summary}")
                self.log(db, invoice.id, f"TallyPrime inventory masters created: {master_response.summary}")
            with tally_mapping_context(rows):
                vendor_response = client.sync_vendor_master(data)
            if vendor_response.success:
                self.log(db, invoice.id, f"TallyPrime vendor master synced: {vendor_response.summary}")
            with tally_mapping_context(rows):
                ledgers_response = client.sync_system_ledgers()
            if ledgers_response.success:
                self.log(db, invoice.id, f"TallyPrime purchase/GST ledgers synced: {ledgers_response.summary}")
            with tally_mapping_context(rows):
                stock_items_response = client.sync_inventory_item_masters(data)
            if stock_items_response.success:
                self.log(db, invoice.id, f"TallyPrime stock item masters synced: {stock_items_response.summary}")
            with tally_mapping_context(rows):
                voucher_response = client.post_inventory_purchase_voucher(invoice.id, data)
            if not voucher_response.success:
                raise ValueError(f"TallyPrime item voucher posting failed: {voucher_response.summary}")
            invoice.status = InvoiceStatus.POSTED
            db.commit()
            self.log(db, invoice.id, f"Pushed item-wise to TallyPrime ({voucher_response.summary}) - status set to Posted")
            return {"success": True, "message": "Invoice items posted to TallyPrime.", "tally_response": voucher_response.summary}

    def sync_vendor_master_to_tally(self, invoice_id: int) -> dict[str, Any]:
        """Update the TallyPrime vendor ledger with extracted invoice vendor details."""
        self.initialize()
        logger.info("Tally vendor master sync requested for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            data = invoice_data_from_invoice(invoice)
            if data is None:
                raise ValueError("No extracted invoice data is available for Tally vendor sync.")
            client = TallyClient()
            self.assert_tally_company_selected(client)
            client.check_connection()
            with tally_mapping_context(self.tally_mapping_rows_for_posting(db, data)):
                response = client.sync_vendor_master(data)
            if not response.success:
                raise ValueError(f"TallyPrime vendor master sync failed: {response.summary}")
            self.log(db, invoice.id, f"TallyPrime vendor master synced: {response.summary}")
            return {"success": True, "message": "Vendor master synced to TallyPrime.", "tally_response": response.summary}

    def sync_tally_system_ledgers(self, invoice_id: int | None = None) -> dict[str, Any]:
        """Update configured purchase and GST ledgers in TallyPrime."""
        self.initialize()
        logger.info("Tally system ledger sync requested")
        with session_scope() as db:
            data = None
            if invoice_id is not None:
                invoice = self.require_invoice(db, invoice_id)
                data = invoice_data_from_invoice(invoice)
            client = TallyClient()
            self.assert_tally_company_selected(client)
            client.check_connection()
            with tally_mapping_context(self.tally_mapping_rows_for_posting(db, data)):
                response = client.sync_system_ledgers()
            if not response.success:
                raise ValueError(f"TallyPrime ledger sync failed: {response.summary}")
            if invoice_id is not None:
                self.log(db, invoice_id, f"TallyPrime purchase/GST ledgers synced: {response.summary}")
            return {"success": True, "message": "Purchase and GST ledgers synced to TallyPrime.", "tally_response": response.summary}
    def run_pipeline(self, db, invoice: Invoice, file_path: Path, progress_callback: ProgressCallback | None = None) -> None:
        """Execute extraction, AI parsing, validation, and persistence."""
        start = time.perf_counter()
        logger.info("Pipeline started for invoice #%s: %s", invoice.id, file_path)
        self.progress(progress_callback, "Starting extraction pipeline...")
        invoice.status = InvoiceStatus.IN_PROCESS
        db.commit()
        self.log(db, invoice.id, "Status set to In_Process")

        self.progress(progress_callback, "Classifying invoice document type...")
        source = classify_document(file_path)
        self.progress(progress_callback, f"{document_kind_label(source.document_kind)} detected.")
        self.log(db, invoice.id, f"Document classified as {source.document_kind.value}", reason=source.mime_type)

        raw_markdown = None
        try:
            if source.document_kind == DocumentKind.DIGITAL_PDF:
                self.progress(progress_callback, "Digital PDF detected. Extracting text and tables...")
                extraction_start = time.perf_counter()
                raw_markdown = extract_invoice_source_text(source)
                extraction_time_ms = int((time.perf_counter() - extraction_start) * 1000)
                logger.info(
                    "PDF extraction finished for invoice #%s in %sms: %d chars",
                    invoice.id,
                    extraction_time_ms,
                    len(raw_markdown or ""),
                )
                self.log(db, invoice.id, f"PDF extraction complete in {extraction_time_ms}ms - {len(raw_markdown or '')} chars")
                logger.info("AI parsing started for invoice #%s", invoice.id)
                self.progress(progress_callback, "Sending invoice text to Gemini for structured extraction...")
                ai_start = time.perf_counter()
                self.record_ai_call(db, invoice)
                parsed = parse_invoice(raw_markdown, vendor_hint=invoice.filename)
            else:
                logger.info("Visual parsing route selected for invoice #%s: %s", invoice.id, source.document_kind.value)
                self.log(db, invoice.id, f"Visual AI parsing route used - {source.document_kind.value}")
                raw_markdown = None
                logger.info("AI parsing started for invoice #%s", invoice.id)
                self.progress(progress_callback, "Preparing visual invoice for Gemini multimodal extraction...")
                ai_start = time.perf_counter()
                self.record_ai_call(db, invoice)
                parsed = parse_invoice_file(source.path, source.mime_type, invoice.filename, document_kind=source.document_kind.value)
            ai_time_ms = int((time.perf_counter() - ai_start) * 1000)
            logger.info("AI parsing finished for invoice #%s in %sms", invoice.id, ai_time_ms)
            self.progress(progress_callback, "Reading AI response and normalizing invoice fields...")
            data = InvoiceData(**parsed)
            self.progress(progress_callback, "Validating GST, totals, and line items...")
            validation = validate_invoice(data, raw_markdown)
        except AIRateLimitError as exc:
            logger.warning("AI quota/rate limit for invoice #%s: %s", invoice.id, exc)
            self.progress(progress_callback, f"Gemini quota or rate limit reached: {exc}", level="warning")
            data = InvoiceData(vendor_name=invoice.filename)
            validation = ValidationResult(
                is_valid=False,
                errors=[str(exc)],
                warnings=[],
                issues=[{"severity": "error", "message": str(exc), "field": "AI Quota"}],
            )
            self.log(db, invoice.id, f"AI quota/rate limit: {exc}")
        except Exception as exc:
            logger.exception("AI parsing failed for invoice #%s", invoice.id)
            self.progress(progress_callback, f"AI parsing failed: {exc}", level="error")
            data = InvoiceData(vendor_name=invoice.filename)
            validation = ValidationResult(
                is_valid=False,
                errors=[str(exc)],
                warnings=[],
                issues=[{"severity": "error", "message": str(exc), "field": "AI Parser"}],
            )
            self.log(db, invoice.id, f"AI parsing failed: {exc}")
        confidence = calculate_confidence_score(data, validation)
        data.confidence_score = confidence

        self.progress(progress_callback, "Saving extracted data, validation results, and audit logs...")
        persist_extraction(
            db,
            invoice,
            data,
            validation,
            raw_markdown,
            document_kind=source.document_kind.value,
            mime_type=source.mime_type,
        )
        invoice.status = InvoiceStatus.PENDING_REVIEW
        invoice.processing_time_ms = int((time.perf_counter() - start) * 1000)
        db.commit()
        logger.info("Pipeline complete for invoice #%s in %sms", invoice.id, invoice.processing_time_ms)
        self.log(db, invoice.id, f"Pipeline complete in {invoice.processing_time_ms}ms - status set to Pending_Review")
        self.progress(progress_callback, "Invoice is ready for review.")

    def invoice_to_record(self, invoice: Invoice, *, db=None, include_tally_mappings: bool = False) -> InvoiceRecord:
        """Convert an ORM invoice into a display-ready Pydantic record."""
        extracted = invoice_data_from_invoice(invoice)
        validation = validation_from_invoice(invoice) if extracted else None
        tally_mappings = []
        if include_tally_mappings and db is not None and extracted is not None:
            tally_mappings = self.tally_mapping_rows_for_review(db, extracted)
        return InvoiceRecord(
            id=invoice.id,
            filename=invoice.filename,
            file_path=invoice.file_path,
            file_hash=invoice.file_hash,
            status=invoice.status,
            supply_type=invoice.supply_type,
            confidence_score=invoice.confidence_score,
            raw_markdown=raw_markdown_from_invoice(invoice),
            extracted_data=extracted,
            validation=validation,
            reviewed_by=invoice.reviewed_by,
            reviewed_at=invoice.reviewed_at,
            processing_time_ms=invoice.processing_time_ms,
            ai_call_count=invoice.ai_call_count or 0,
            reprocess_count=invoice.reprocess_count or 0,
            rejection_reason=invoice.rejection_reason,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
            tally_mappings=tally_mappings,
        )

    def tally_mapping_rows_for_posting(self, db, data: InvoiceData | None = None) -> list[dict[str, Any]]:
        """Return SQL mapping rows for direct Tally XML generation."""
        company = get_tally_settings().tally_company.strip()
        if data is None:
            return context_rows_for_settings(db, company)
        return context_rows_for_invoice(db, data, company)
    def tally_mapping_rows_for_review(self, db, data: InvoiceData) -> list[dict[str, Any]]:
        """Return editable invoice-level mapping rows with best-effort Tally suggestions."""
        settings = get_tally_settings()
        company = settings.tally_company.strip()
        candidates: dict[str, list[str]] = {VENDOR_LEDGER: [], STOCK_ITEM: [], UNIT: []}
        if company:
            try:
                client = TallyClient(url=settings.tally_url, timeout=min(settings.tally_timeout_seconds, 5))
                candidates[VENDOR_LEDGER] = sorted(
                    client.fetch_master_names("InvoiceAIReviewVendorLedgers", "Ledger", company=company),
                    key=str.casefold,
                )
                candidates[STOCK_ITEM] = sorted(
                    client.fetch_master_names("InvoiceAIReviewStockItems", "Stock Item", company=company),
                    key=str.casefold,
                )
                candidates[UNIT] = sorted(
                    client.fetch_master_names("InvoiceAIReviewUnits", "Unit", company=company),
                    key=str.casefold,
                )
            except Exception as exc:
                logger.info("Tally mapping suggestions unavailable: %s", exc)
        return dynamic_mapping_rows(db, data, company, candidates=candidates)
    def require_invoice(self, db, invoice_id: int) -> Invoice:
        """Fetch an invoice or raise a user-facing error."""
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        return invoice

    def record_ai_call(self, db, invoice: Invoice) -> None:
        """Increment durable per-invoice AI usage before invoking the parser."""
        invoice.ai_call_count = (invoice.ai_call_count or 0) + 1
        db.commit()
        self.log(db, invoice.id, f"AI client call #{invoice.ai_call_count}")

    def log(self, db, invoice_id: int, action: str, reason: str | None = None, user: str = "system") -> None:
        """Persist one audit log row and emit the same event to app logs."""
        logger.info("Audit invoice #%s | %s | %s", invoice_id, user, action)
        db.add(AuditLog(invoice_id=invoice_id, user=user, action=action, reason=reason))
        db.commit()

    def assert_tally_company_selected(self, client: TallyClient) -> None:
        """Block direct Tally actions unless the selected company is available."""
        verify_tally_company_selected(client)
    def progress(self, callback: ProgressCallback | None, message: str, *, level: str = "info") -> None:
        """Emit one optional user-facing processing progress event."""
        if callback:
            callback({"message": message, "level": level})

    def unique_upload_path(self, filename: str) -> Path:
        """Return a non-conflicting path inside the uploads directory."""
        target = UPLOAD_DIR / filename
        if not target.exists():
            return target
        stem = target.stem
        suffix = target.suffix
        counter = 1
        while True:
            candidate = UPLOAD_DIR / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1
