from __future__ import annotations

"""In-process application workflow used by the PySide6 desktop UI."""

import logging
import hashlib
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from .exports.exporters import export_invoice_csv, export_invoice_json, export_invoice_tally, export_to_erpnext
from .parsing.ai_parser import parse_invoice_source
from ..domain.validation import calculate_confidence_score, validate_invoice

logger = logging.getLogger(__name__)


class DesktopWorkflow:
    """Facade for all invoice operations used by the desktop UI."""

    def initialize(self) -> None:
        """Initialize the local desktop database."""
        if getattr(self, "_initialized", False):
            return
        logger.info("Initializing desktop database")
        init_db()
        self._initialized = True

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
            return self.invoice_to_record(invoice).model_dump(mode="json")

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

    def upload_invoice(self, source_path: str | Path) -> dict[str, Any]:
        """Copy, process, persist, and return a newly uploaded invoice."""
        self.initialize()
        source = Path(source_path)
        logger.info("Upload requested: %s", source)
        validate_upload_file(source)
        file_hash = sha256_file(source)
        target = self.unique_upload_path(source.name)
        shutil.copy2(source, target)

        with session_scope() as db:
            invoice = Invoice(filename=source.name, file_path=str(target), file_hash=file_hash, status=InvoiceStatus.NEW)
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            self.log(db, invoice.id, "Invoice uploaded", reason=f"File: {source.name}")
            if DUPLICATE_CHECK_ENABLED:
                duplicate = db.scalar(
                    select(Invoice.id).where(Invoice.file_hash == file_hash, Invoice.id != invoice.id)
                )
                if duplicate:
                    self.log(db, invoice.id, f"WARNING: Duplicate file content detected (matches invoice #{duplicate})")
            try:
                self.run_pipeline(db, invoice, target)
            except ScannedDocumentException as exc:
                invoice.status = InvoiceStatus.PENDING_REVIEW
                db.commit()
                self.log(db, invoice.id, f"Pipeline extraction route error: {exc}")
            except Exception as exc:
                logger.exception("Pipeline error for invoice %s", invoice.id)
                invoice.status = InvoiceStatus.PENDING_REVIEW
                db.commit()
                self.log(db, invoice.id, f"Pipeline error: {exc}")
            db.refresh(invoice)
            return self.invoice_to_record(invoice).model_dump(mode="json")

    def reprocess_invoice(self, invoice_id: int) -> dict[str, Any]:
        """Run the extraction pipeline again for an existing invoice."""
        self.initialize()
        logger.info("Reprocess requested for invoice #%s", invoice_id)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            path = Path(invoice.file_path)
            if not path.exists():
                raise FileNotFoundError(f"Original file not found: {path}")
            self.log(db, invoice.id, "Reprocessing triggered", user="human")
            self.run_pipeline(db, invoice, path)
            db.refresh(invoice)
            return self.invoice_to_record(invoice).model_dump(mode="json")

    def submit_review(self, invoice_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply a reviewer decision and return the updated invoice."""
        self.initialize()
        review = InvoiceReviewRequest(**payload)
        logger.info("Review submitted for invoice #%s: %s", invoice_id, review.decision.value)
        with session_scope() as db:
            invoice = self.require_invoice(db, invoice_id)
            if invoice.status not in {InvoiceStatus.PENDING_REVIEW, InvoiceStatus.EXTRACTED, InvoiceStatus.REJECTED}:
                raise ValueError(f"Cannot review invoice in '{invoice.status}' status")
            now = datetime.now(timezone.utc)
            if review.decision == ReviewDecision.APPROVE:
                invoice.status = InvoiceStatus.APPROVED
                invoice.reviewed_by = review.reviewer
                invoice.reviewed_at = now
                self.log(db, invoice.id, "HITL: Invoice APPROVED - status set to Approved", user=review.reviewer)
            elif review.decision == ReviewDecision.APPROVE_WITH_CORRECTIONS:
                current_data = invoice_data_from_invoice(invoice) or InvoiceData()
                current = current_data.model_dump(mode="json")
                changed = []
                for field, value in (review.corrections or {}).items():
                    if current.get(field) != value:
                        self.log(db, invoice.id, f"HITL: Field '{field}' corrected", reason="Manual correction during review", user=review.reviewer)
                        current[field] = value
                        changed.append(field)
                data = InvoiceData(**current)
                raw_markdown = raw_markdown_from_invoice(invoice)
                validation = validate_invoice(data, raw_markdown)
                document_kind = invoice.extraction.document_kind if invoice.extraction else None
                mime_type = invoice.extraction.mime_type if invoice.extraction else None
                persist_extraction(db, invoice, data, validation, raw_markdown, document_kind=document_kind, mime_type=mime_type)
                invoice.status = InvoiceStatus.APPROVED
                invoice.reviewed_by = review.reviewer
                invoice.reviewed_at = now
                self.log(db, invoice.id, f"HITL: Invoice APPROVED WITH CORRECTIONS - {len(changed)} field(s) changed", user=review.reviewer)
            elif review.decision == ReviewDecision.REJECT:
                reason = review.rejection_reason or "No reason provided"
                invoice.status = InvoiceStatus.REJECTED
                invoice.reviewed_by = review.reviewer
                invoice.reviewed_at = now
                invoice.rejection_reason = reason
                self.log(db, invoice.id, f"HITL: Invoice REJECTED - {reason}", reason=reason, user=review.reviewer)
            db.commit()
            db.refresh(invoice)
            return self.invoice_to_record(invoice).model_dump(mode="json")

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
            if fmt == "csv":
                content, filename = export_invoice_csv(invoice_id, data)
            elif fmt == "json":
                content, filename = export_invoice_json(invoice_id, data)
            elif fmt == "tally":
                content, filename = export_invoice_tally(invoice_id, data)
            elif fmt == "erpnext":
                result = export_to_erpnext(data)
                if not result.get("success"):
                    raise ValueError(result.get("error", "ERPNext export failed"))
                invoice.status = InvoiceStatus.POSTED
                db.commit()
                self.log(db, invoice.id, f"Pushed to ERPNext (Ref: {result.get('erp_reference')}) - status set to Posted")
                return result, None
            else:
                raise ValueError(f"Unsupported export format: {fmt}")
            return content, filename

    def run_pipeline(self, db, invoice: Invoice, file_path: Path) -> None:
        """Execute extraction, AI parsing, validation, and persistence."""
        start = time.perf_counter()
        logger.info("Pipeline started for invoice #%s: %s", invoice.id, file_path)
        invoice.status = InvoiceStatus.IN_PROCESS
        db.commit()
        self.log(db, invoice.id, "Status set to In_Process")

        source = classify_document(file_path)
        self.log(db, invoice.id, f"Document classified as {source.document_kind.value}", reason=source.mime_type)

        try:
            parsed_result = parse_invoice_source(source, vendor_hint=invoice.filename)
            raw_markdown = parsed_result.source_text
            if source.document_kind == DocumentKind.DIGITAL_PDF:
                logger.info("PDF extraction finished for invoice #%s: %d chars", invoice.id, len(raw_markdown or ""))
                self.log(db, invoice.id, f"PDF extraction complete - {len(raw_markdown or '')} chars")
            else:
                logger.info("Visual parsing route selected for invoice #%s: %s", invoice.id, source.document_kind.value)
                self.log(db, invoice.id, f"Visual AI parsing route used - {source.document_kind.value}")
            parsed = parsed_result.data
            logger.info("AI parsing finished for invoice #%s", invoice.id)
            data = InvoiceData(**parsed)
            validation = validate_invoice(data, raw_markdown)
        except Exception as exc:
            logger.exception("AI parsing failed for invoice #%s", invoice.id)
            data = InvoiceData(vendor_name=invoice.filename)
            validation = ValidationResult(
                is_valid=False,
                errors=[str(exc)],
                warnings=[],
                issues=[{"severity": "error", "message": str(exc), "field": "AI Parser"}],
            )
            self.log(db, invoice.id, f"AI parsing failed: {exc}")
            raw_markdown = None
            parsed_result = None
        confidence = calculate_confidence_score(data, validation)
        data.confidence_score = confidence

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

    def invoice_to_record(self, invoice: Invoice) -> InvoiceRecord:
        """Convert an ORM invoice into a display-ready Pydantic record."""
        extracted = invoice_data_from_invoice(invoice)
        validation = validation_from_invoice(invoice) if extracted else None
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
            rejection_reason=invoice.rejection_reason,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
        )

    def require_invoice(self, db, invoice_id: int) -> Invoice:
        """Fetch an invoice or raise a user-facing error."""
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        return invoice

    def log(self, db, invoice_id: int, action: str, reason: str | None = None, user: str = "system") -> None:
        """Persist one audit log row and emit the same event to app logs."""
        logger.info("Audit invoice #%s | %s | %s", invoice_id, user, action)
        db.add(AuditLog(invoice_id=invoice_id, user=user, action=action, reason=reason))
        db.commit()

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


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hash for a file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
