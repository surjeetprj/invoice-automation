"""
Human-in-the-Loop (HITL) Reviewer Service.

Handles the review workflow for invoice extraction:
- Assemble review payload with side-by-side data
- Process approve / approve_with_corrections / reject decisions
- Flag low-confidence fields for reviewer attention
- Full audit trail for every action

AI extraction is NEVER trusted blindly — every invoice must pass through
human review before it can be exported to ERP.
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from config import InvoiceStatus, MIN_CONFIDENCE_SCORE
from models import AuditLog, Invoice
from schemas import (
    FieldConfidence,
    InvoiceData,
    InvoiceReviewRequest,
    InvoiceReviewResponse,
    ReviewDecision,
    ValidationResult,
)
from services.validator import validate_invoice

logger = logging.getLogger(__name__)


def flag_low_confidence_fields(data: InvoiceData) -> list[FieldConfidence]:
    """
    Analyse extracted data and flag fields that likely need human attention.

    Heuristics:
    - Missing critical fields (invoice_number, vendor_gstin, etc.) → confidence 0.0
    - Fields present → base confidence from overall confidence_score
    - Empty optional fields → not flagged (acceptable)
    """
    flags: list[FieldConfidence] = []
    base = data.confidence_score if data.confidence_score > 0 else 0.5

    critical_fields = {
        "invoice_number": data.invoice_number,
        "date": data.date,
        "vendor_name": data.vendor_name,
        "vendor_gstin": data.vendor_gstin,
        "customer_name": data.customer_name,
        "customer_gstin": data.customer_gstin,
        "total_amount": str(data.total_amount) if data.total_amount > 0 else None,
        "place_of_supply": data.place_of_supply,
    }

    for field_name, value in critical_fields.items():
        if not value:
            flags.append(FieldConfidence(
                field_name=field_name,
                value=None,
                confidence=0.0,
                needs_review=True,
            ))
        else:
            conf = min(base + 0.1, 1.0)  # Slightly boost present fields
            needs_review = conf < MIN_CONFIDENCE_SCORE
            flags.append(FieldConfidence(
                field_name=field_name,
                value=str(value)[:100],
                confidence=round(conf, 2),
                needs_review=needs_review,
            ))

    # Flag line items if none found
    if not data.line_items:
        flags.append(FieldConfidence(
            field_name="line_items",
            value="0 items",
            confidence=0.0,
            needs_review=True,
        ))

    # Flag tax info
    has_tax = (
        data.total_cgst > 0 or data.total_sgst > 0 or
        data.total_igst > 0 or data.total_tax_amount > 0
    )
    if not has_tax:
        flags.append(FieldConfidence(
            field_name="tax_breakup",
            value="No tax found",
            confidence=0.0,
            needs_review=True,
        ))

    return flags


def get_review_payload(invoice: Invoice) -> dict:
    """
    Assemble the complete review payload for the HITL reviewer.

    Includes:
    - Extracted data (structured)
    - Raw markdown (for side-by-side comparison)
    - Validation results (errors + warnings)
    - Field-level confidence flags
    """
    extracted_data = None
    validation = None
    field_flags = []

    if invoice.extracted_data:
        try:
            parsed = json.loads(invoice.extracted_data)
            extracted_data = InvoiceData(**parsed)

            # Run validation
            validation = validate_invoice(extracted_data)

            # Flag low-confidence fields
            field_flags = flag_low_confidence_fields(extracted_data)
        except Exception as e:
            logger.error("Failed to parse extracted data for review: %s", e)

    if invoice.validation_result:
        try:
            validation = ValidationResult(**json.loads(invoice.validation_result))
        except Exception:
            pass

    return {
        "invoice_id": invoice.id,
        "filename": invoice.filename,
        "status": invoice.status,
        "raw_markdown": invoice.raw_markdown,
        "extracted_data": extracted_data.model_dump() if extracted_data else None,
        "validation": validation.model_dump() if validation else None,
        "field_confidences": [fc.model_dump() for fc in field_flags],
        "confidence_score": invoice.confidence_score,
        "processing_time_ms": invoice.processing_time_ms,
        "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
    }


async def apply_review_decision(
    invoice: Invoice,
    review: InvoiceReviewRequest,
    db: AsyncSession,
) -> InvoiceReviewResponse:
    """
    Process the human reviewer's decision.

    - APPROVE: set status to Approved, log approval
    - APPROVE_WITH_CORRECTIONS: apply corrections, re-validate, set Approved
    - REJECT: set status to Rejected, record reason
    """
    now = datetime.now(timezone.utc)

    if review.decision == ReviewDecision.APPROVE:
        # ── Approve as-is ────────────────────
        invoice.status = InvoiceStatus.APPROVED
        invoice.reviewed_by = review.reviewer
        invoice.reviewed_at = now

        db.add(AuditLog(
            invoice_id=invoice.id,
            user=review.reviewer,
            action="HITL: Invoice APPROVED — status set to Approved",
        ))

        await db.commit()
        await db.refresh(invoice)

        return InvoiceReviewResponse(
            id=invoice.id,
            status=invoice.status,
            decision=review.decision.value,
            message="Invoice approved and marked as Approved.",
        )

    elif review.decision == ReviewDecision.APPROVE_WITH_CORRECTIONS:
        # ── Apply corrections then approve ───
        current_data: dict = {}
        if invoice.extracted_data:
            current_data = json.loads(invoice.extracted_data)

        corrections = review.corrections or {}
        changed_fields = []

        for field, new_value in corrections.items():
            old_value = current_data.get(field)
            if old_value != new_value:
                changed_fields.append(field)
                current_data[field] = new_value

                # Log each field change
                old_str = json.dumps(old_value) if isinstance(old_value, (list, dict)) else str(old_value)
                new_str = json.dumps(new_value) if isinstance(new_value, (list, dict)) else str(new_value)
                db.add(AuditLog(
                    invoice_id=invoice.id,
                    user=review.reviewer,
                    action=f"HITL: Field '{field}' corrected from {old_str} to {new_str}",
                    reason="Manual correction during review",
                ))

        # Save corrected data
        invoice.extracted_data = json.dumps(current_data)

        # Re-validate with corrections
        invoice_data = InvoiceData(**current_data)
        validation = validate_invoice(invoice_data)

        # Update denormalized fields
        invoice.invoice_number_extracted = invoice_data.invoice_number
        invoice.vendor_gstin = invoice_data.vendor_gstin
        invoice.supply_type = invoice_data.supply_type.value if invoice_data.supply_type else None

        # Set status
        invoice.status = InvoiceStatus.APPROVED
        invoice.reviewed_by = review.reviewer
        invoice.reviewed_at = now
        invoice.validation_result = json.dumps(validation.model_dump())

        db.add(AuditLog(
            invoice_id=invoice.id,
            user=review.reviewer,
            action=(
                f"HITL: Invoice APPROVED WITH CORRECTIONS — "
                f"{len(changed_fields)} field(s) changed: {', '.join(changed_fields)}. "
                f"Status set to Approved."
            ),
        ))

        await db.commit()
        await db.refresh(invoice)

        return InvoiceReviewResponse(
            id=invoice.id,
            status=invoice.status,
            decision=review.decision.value,
            message=f"Invoice approved with {len(changed_fields)} correction(s). Marked as Approved.",
            validation=validation,
        )

    elif review.decision == ReviewDecision.REJECT:
        # ── Reject ───────────────────────────
        rejection_reason = review.rejection_reason or "No reason provided"

        invoice.status = InvoiceStatus.REJECTED
        invoice.reviewed_by = review.reviewer
        invoice.reviewed_at = now
        invoice.rejection_reason = rejection_reason

        db.add(AuditLog(
            invoice_id=invoice.id,
            user=review.reviewer,
            action=f"HITL: Invoice REJECTED — {rejection_reason}",
            reason=rejection_reason,
        ))

        await db.commit()
        await db.refresh(invoice)

        return InvoiceReviewResponse(
            id=invoice.id,
            status=invoice.status,
            decision=review.decision.value,
            message=f"Invoice rejected. Reason: {rejection_reason}",
        )

    else:
        raise ValueError(f"Unknown review decision: {review.decision}")
