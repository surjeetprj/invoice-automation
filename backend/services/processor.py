"""
Invoice Processing Engine — Core pipeline execution and response mapping.

This module decouples the business logic from the API routing layer.
It contains:
    - _invoice_to_response: Converts raw SQLAlchemy Invoice ORM objects into
      structured Pydantic API response models (InvoiceResponse).
    - _log: Records timestamped audit trail entries against an invoice.
    - _run_pipeline: Orchestrates the full ingestion lifecycle:
      pdfplumber extraction → Gemini AI parsing → GST validation → status update.
"""
import json
import logging
import time
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from config import InvoiceStatus
from models import AuditLog, Invoice
from schemas import InvoiceData, InvoiceResponse, ValidationResult
from services.doc_extraction_engine import extract_with_metadata
from services.ai_parser import parse_invoice
from services.validator import validate_invoice, calculate_confidence_score

logger = logging.getLogger(__name__)

def _invoice_to_response(inv: Invoice) -> InvoiceResponse:
    """
    Convert a raw SQLAlchemy Invoice ORM object into a structured Pydantic
    InvoiceResponse model suitable for API serialization.

    The Invoice table stores extracted_data and validation_result as raw JSON
    strings. This function deserializes those JSON blobs back into their
    respective Pydantic models (InvoiceData, ValidationResult) so that the
    API response is fully typed and validated.

    If deserialization fails (e.g. corrupted or partially saved JSON), the
    corresponding field is set to None instead of raising an exception,
    ensuring the API always returns a valid response object.

    Args:
        inv: The SQLAlchemy Invoice ORM instance fetched from the database.

    Returns:
        InvoiceResponse: A fully structured Pydantic response model containing
        invoice metadata, parsed fields, validation results, review status,
        and processing timestamps.
    """
    extracted = None
    validation = None
    if inv.extracted_data:
        try:
            extracted = InvoiceData(**json.loads(inv.extracted_data))
        except Exception:
            extracted = None
    if inv.validation_result:
        try:
            validation = ValidationResult(**json.loads(inv.validation_result))
        except Exception:
            validation = None

    return InvoiceResponse(
        id=inv.id, filename=inv.filename, status=inv.status,
        supply_type=inv.supply_type, confidence_score=inv.confidence_score,
        raw_markdown=inv.raw_markdown, extracted_data=extracted,
        validation=validation, reviewed_by=inv.reviewed_by,
        reviewed_at=inv.reviewed_at, processing_time_ms=inv.processing_time_ms,
        created_at=inv.created_at, updated_at=inv.updated_at,
    )


async def _log(db: AsyncSession, invoice_id: int, action: str, reason: str = None, user: str = "system"):
    """
    Record a timestamped audit trail entry against a specific invoice.

    Every significant pipeline event (upload, extraction, validation,
    review decision, field correction, export) is logged here to maintain
    a complete, queryable history of actions taken on each invoice.

    Args:
        db: Active async database session.
        invoice_id: The ID of the invoice being tracked.
        action: A human-readable description of the event (e.g. "AI parsing complete").
        reason: Optional context or justification (e.g. correction reason from a reviewer).
        user: The actor performing the action. Defaults to "system" for automated steps.
    """
    db.add(AuditLog(invoice_id=invoice_id, user=user, action=action, reason=reason))
    await db.commit()


async def _run_pipeline(invoice: Invoice, file_path: Path, db: AsyncSession):
    """
    Execute the full invoice ingestion pipeline end-to-end.

    Pipeline stages:
        1. Status update to In_Process.
        2. pdfplumber layout-preserving text extraction (spatial coordinate parsing).
        3. AI structured parsing via Gemini 2.5 Flash (maps raw text → Pydantic schema).
        4. GST compliance validation (HSN slabs, tax math, state code cross-checks).
        5. Confidence score calculation and status update to Pending_Review.

    All intermediate results are persisted to the database after each step,
    and audit log entries are recorded for full traceability.

    Args:
        invoice: The SQLAlchemy Invoice ORM instance to process.
        file_path: Absolute path to the uploaded PDF file on disk.
        db: Active async database session for persistence and audit logging.
    """
    start = time.perf_counter()

    # Step 1: In_Process
    invoice.status = InvoiceStatus.IN_PROCESS
    await db.commit()
    await _log(db, invoice.id, "Status → In_Process")

    # Step 2: pdfplumber layout-preserving text extraction
    raw_markdown, meta = extract_with_metadata(str(file_path))
    invoice.raw_markdown = raw_markdown
    await db.commit()
    quality = f"{meta.character_count} chars, {meta.page_count} pages, {meta.table_count} tables"
    await _log(db, invoice.id, f"pdfplumber extraction complete — {quality}")

    # Step 3: AI Parse
    parsed = parse_invoice(raw_markdown)
    invoice.extracted_data = json.dumps(parsed)
    await db.commit()
    await _log(db, invoice.id, "AI parsing complete")

    # Step 4: Validate & calculate confidence
    invoice_data = InvoiceData(**parsed)
    result = validate_invoice(invoice_data)
    confidence = calculate_confidence_score(invoice_data, result)
    invoice.validation_result = json.dumps(result.model_dump())

    # Denormalize for queries
    invoice.invoice_number_extracted = invoice_data.invoice_number
    invoice.vendor_gstin = invoice_data.vendor_gstin
    invoice.supply_type = invoice_data.supply_type.value if invoice_data.supply_type else None
    invoice.confidence_score = confidence  # Calculated, not LLM-reported

    # Step 5: Set to Pending_Review (NOT directly to Approved)
    invoice.status = InvoiceStatus.PENDING_REVIEW
    elapsed = int((time.perf_counter() - start) * 1000)
    invoice.processing_time_ms = elapsed

    await db.commit()
    msg = f"Pipeline complete in {elapsed}ms — status → Pending_Review"
    if result.errors:
        msg += f". Validation errors: {'; '.join(result.errors[:3])}"
    if result.warnings:
        msg += f". Warnings: {len(result.warnings)}"
    await _log(db, invoice.id, msg)
