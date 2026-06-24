from __future__ import annotations

"""Review and correction persistence helpers for DesktopWorkflow."""

from collections.abc import Callable
from datetime import datetime

from ..config import InvoiceStatus
from ..db.models import Invoice
from ..db.repository import invoice_data_from_invoice, persist_extraction, raw_markdown_from_invoice
from ..domain.schemas import InvoiceData, InvoiceReviewRequest, ReviewDecision
from ..domain.validation import validate_invoice
from .settings import get_tally_settings
from .tally.mapping import save_mappings

AuditWriter = Callable[[int, str, str | None, str], None]

REVIEWABLE_STATUSES = {InvoiceStatus.PENDING_REVIEW, InvoiceStatus.EXTRACTED, InvoiceStatus.REJECTED}
CORRECTION_STATUSES = REVIEWABLE_STATUSES | {InvoiceStatus.APPROVED, InvoiceStatus.POSTED}


def ensure_review_allowed(invoice: Invoice, decision: ReviewDecision) -> None:
    """Raise when the requested review decision is not allowed for the invoice status."""
    if decision == ReviewDecision.SAVE_CORRECTIONS:
        if invoice.status not in CORRECTION_STATUSES:
            raise ValueError(f"Cannot save corrections for invoice in '{invoice.status}' status")
    elif invoice.status not in REVIEWABLE_STATUSES:
        raise ValueError(f"Cannot review invoice in '{invoice.status}' status")


def apply_review_decision(db, invoice: Invoice, review: InvoiceReviewRequest, now: datetime, audit: AuditWriter) -> None:
    """Apply one review decision, including correction persistence and audit events."""
    if review.decision == ReviewDecision.APPROVE:
        invoice.status = InvoiceStatus.APPROVED
        invoice.reviewed_by = review.reviewer
        invoice.reviewed_at = now
        audit(invoice.id, "HITL: Invoice APPROVED - status set to Approved", None, review.reviewer)
        return

    if review.decision in {ReviewDecision.SAVE_CORRECTIONS, ReviewDecision.APPROVE_WITH_CORRECTIONS}:
        changed = persist_review_corrections(db, invoice, review, audit)
        if review.decision == ReviewDecision.SAVE_CORRECTIONS:
            audit(invoice.id, f"HITL: Corrections saved - {len(changed)} field(s) changed", None, review.reviewer)
            return
        invoice.status = InvoiceStatus.APPROVED
        invoice.reviewed_by = review.reviewer
        invoice.reviewed_at = now
        audit(invoice.id, f"HITL: Invoice APPROVED WITH CORRECTIONS - {len(changed)} field(s) changed", None, review.reviewer)
        return

    if review.decision == ReviewDecision.REJECT:
        reason = review.rejection_reason or "No reason provided"
        invoice.status = InvoiceStatus.REJECTED
        invoice.reviewed_by = review.reviewer
        invoice.reviewed_at = now
        invoice.rejection_reason = reason
        audit(invoice.id, f"HITL: Invoice REJECTED - {reason}", reason, review.reviewer)


def persist_review_corrections(db, invoice: Invoice, review: InvoiceReviewRequest, audit: AuditWriter) -> list[str]:
    """Persist reviewer corrections, mapping edits, and refreshed validation rows."""
    corrections = dict(review.corrections or {})
    mapping_rows = corrections.pop("tally_mappings", [])
    current_data = invoice_data_from_invoice(invoice) or InvoiceData()
    current = current_data.model_dump(mode="json")
    changed: list[str] = []
    for field, value in corrections.items():
        if current.get(field) != value:
            audit(invoice.id, f"HITL: Field '{field}' corrected", "Manual correction during review", review.reviewer)
            current[field] = value
            changed.append(field)
    if mapping_rows:
        company_name = mapping_company_name(mapping_rows)
        saved = save_mappings(db, company_name, mapping_rows)
        if saved:
            audit(invoice.id, f"HITL: Tally mappings saved - {saved} row(s)", "Manual mapping during review", review.reviewer)
            changed.append("tally_mappings")
    data = InvoiceData(**current)
    raw_markdown = raw_markdown_from_invoice(invoice)
    validation = validate_invoice(data, raw_markdown)
    document_kind = invoice.extraction.document_kind if invoice.extraction else None
    mime_type = invoice.extraction.mime_type if invoice.extraction else None
    persist_extraction(db, invoice, data, validation, raw_markdown, document_kind=document_kind, mime_type=mime_type)
    return changed


def mapping_company_name(mapping_rows: list[dict]) -> str:
    """Return the intended company for submitted review mapping rows."""
    explicit_rows = [row for row in mapping_rows if isinstance(row, dict) and "company_name" in row]
    if explicit_rows and any(not str(row.get("company_name") or "").strip() for row in explicit_rows):
        raise ValueError("Tally mapping corrections must include a Tally company.")
    companies = {
        str(row.get("company_name") or "").strip()
        for row in explicit_rows
        if str(row.get("company_name") or "").strip()
    }
    if len(companies) > 1:
        raise ValueError("Tally mapping corrections must target one Tally company.")
    if companies:
        return next(iter(companies))
    return (get_tally_settings().tally_company or "").strip()
