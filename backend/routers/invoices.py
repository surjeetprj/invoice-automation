import json
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, Response
from fastapi.responses import FileResponse
from sqlalchemy import select, and_, or_
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    ALLOWED_EXTENSIONS, UPLOAD_DIR, DUPLICATE_CHECK_ENABLED,
    MAX_FILE_SIZE_MB, InvoiceStatus,
)
from database import get_db
from models import AuditLog, Invoice
from schemas import (
    AuditLogResponse, BatchProcessResponse, BatchProcessResult,
    InvoiceData, InvoiceResponse, InvoiceReviewRequest,
    InvoiceReviewResponse, InvoiceUpdate, InvoiceListResponse,
)
from services.exporter import export_invoice_csv, export_invoice_json
from services.tally_exporter import export_invoice_tally
from services.erpnext_exporter import export_to_erpnext
from services.doc_extraction_engine import ScannedDocumentException
from services.reviewer import apply_review_decision, get_review_payload
from services.validator import validate_invoice
from services.processor import _invoice_to_response, _log, _run_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/invoices",
    tags=["Invoices"]
)

# NOTE: The process-invoice and process-invoices/batch endpoints are at root in main.py,
# but we can group them here. To keep the exact path compatibility, we will:
# 1. Register `/process-invoice` on invoices router (which makes it /invoices/process-invoice), OR
# 2. Register them on a separate base prefix, or use path overrides.
# Let's register `/process-invoice` and `/process-invoices/batch` at the root path of this router
# but customize their route paths so they match main.py perfectly!
# If we define them on invoices router with path overrides or register them under APIRouter without a prefix,
# that would be even easier.
# Let's register all invoice-specific routes under router = APIRouter(tags=["Invoices"]).
# If we don't put a prefix="/invoices", we can define paths exactly as they are in main.py, e.g.:
# `/process-invoice`
# `/process-invoices/batch`
# `/invoices`
# `/invoices/{invoice_id}`
# This is incredibly clean because it preserves the exact API paths (no breaking changes for the frontend!).
# Let's set prefix="" (default empty prefix) but keep tags=["Invoices"]. That's extremely smart.

from routers.auth import verify_api_key

# Let's change the router initialization:
router = APIRouter(tags=["Invoices"], dependencies=[Depends(verify_api_key)])


# ── POST /process-invoice ─────────────────────
@router.post("/process-invoice", response_model=InvoiceResponse, status_code=201)
async def process_invoice(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload an invoice and run the full pipeline. Ends at Pending_Review."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{suffix}'.")

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(400, f"File exceeds {MAX_FILE_SIZE_MB} MB limit.")

    file_path = UPLOAD_DIR / file.filename
    file_path.write_bytes(contents)
    logger.info("Saved: %s (%d bytes)", file_path.name, len(contents))

    invoice = Invoice(filename=file.filename, status=InvoiceStatus.NEW)
    db.add(invoice)
    await db.commit()
    await db.refresh(invoice)
    await _log(db, invoice.id, "Invoice uploaded", reason=f"File: {file.filename}")

    # Duplicate check
    if DUPLICATE_CHECK_ENABLED:
        dup = await db.execute(
            select(Invoice).where(
                and_(Invoice.filename == file.filename, Invoice.id != invoice.id)
            )
        )
        if dup.first():
            await _log(db, invoice.id, f"WARNING: Duplicate filename '{file.filename}' detected")

    try:
        await _run_pipeline(invoice, file_path, db)
    except ScannedDocumentException as exc:
        logger.warning("Scanned document detected for invoice %d: %s", invoice.id, exc)
        invoice.status = InvoiceStatus.REJECTED  # Mark as Rejected
        await db.commit()
        await _log(db, invoice.id, f"Pipeline rejected: {exc}")
        raise HTTPException(
            status_code=400,
            detail=f"SCANNED_DOCUMENT: {exc}"
        )
    except Exception as exc:
        logger.exception("Pipeline error for invoice %d", invoice.id)
        invoice.status = InvoiceStatus.PENDING_REVIEW  # Still needs review
        await db.commit()
        await _log(db, invoice.id, f"Pipeline error: {exc}")

    await db.refresh(invoice)
    return _invoice_to_response(invoice)


# ── POST /process-invoices/batch ──────────────
@router.post("/process-invoices/batch", response_model=BatchProcessResponse, status_code=201)
async def process_invoices_batch(
    files: list[UploadFile] = File(
        ..., description="Invoice files (PDF, JPG, JPEG, PNG)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Upload and process multiple invoice files (PDF/JPG/PNG). Use 'Add item' to add more files."""
    results = []
    for file in files:
        try:
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                results.append(BatchProcessResult(
                    filename=file.filename or "unknown", status="Failed",
                    error=f"Unsupported type '{suffix}'",
                ))
                continue

            contents = await file.read()
            file_path = UPLOAD_DIR / file.filename
            file_path.write_bytes(contents)

            inv = Invoice(filename=file.filename, status=InvoiceStatus.NEW)
            db.add(inv)
            await db.commit()
            await db.refresh(inv)
            await _log(db, inv.id, "Batch upload", reason=f"File: {file.filename}")

            await _run_pipeline(inv, file_path, db)
            await db.refresh(inv)

            results.append(BatchProcessResult(
                filename=file.filename, invoice_id=inv.id, status=inv.status,
            ))
        except ScannedDocumentException as exc:
            logger.warning("Scanned document detected in batch for %s: %s", file.filename, exc)
            inv.status = InvoiceStatus.REJECTED
            await db.commit()
            await _log(db, inv.id, f"Pipeline rejected: {exc}")
            results.append(BatchProcessResult(
                filename=file.filename or "unknown", status="Failed",
                error=f"SCANNED_DOCUMENT: {exc}",
            ))
        except Exception as exc:
            logger.exception("Batch error for %s", file.filename)
            results.append(BatchProcessResult(
                filename=file.filename or "unknown", status="Failed", error=str(exc),
            ))

    successful = sum(1 for r in results if r.status != "Failed")
    return BatchProcessResponse(
        total_files=len(files), successful=successful,
        failed=len(files) - successful, results=results,
    )


# ── GET /invoices ─────────────────────────────
@router.get("/invoices", response_model=InvoiceListResponse)
async def list_invoices(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List invoices with optional status filter."""
    query = select(Invoice)
    count_query = select(sa_func.count(Invoice.id))

    if status:
        query = query.where(Invoice.status == status)
        count_query = count_query.where(Invoice.status == status)

    total = (await db.execute(count_query)).scalar() or 0
    rows = (await db.execute(
        query.order_by(Invoice.created_at.desc()).offset(skip).limit(limit)
    )).scalars().all()

    return InvoiceListResponse(
        total=total, invoices=[_invoice_to_response(i) for i in rows],
    )


# ── GET /invoices/{id} ────────────────────────
@router.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return _invoice_to_response(inv)


# ── GET /invoices/{id}/review ─────────────────
@router.get("/invoices/{invoice_id}/review")
async def get_invoice_review(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """Get invoice data for HITL review (side-by-side: raw markdown + extracted data + validation)."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return get_review_payload(inv)


# ── POST /invoices/{id}/review ────────────────
@router.post("/invoices/{invoice_id}/review", response_model=InvoiceReviewResponse)
async def submit_invoice_review(
    invoice_id: int,
    review: InvoiceReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Submit HITL review decision: approve, approve_with_corrections, or reject."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status not in (InvoiceStatus.PENDING_REVIEW, InvoiceStatus.EXTRACTED, InvoiceStatus.REJECTED):
        raise HTTPException(400, f"Cannot review invoice in '{inv.status}' status")
    return await apply_review_decision(inv, review, db)


# ── GET /invoices/{id}/file ─────────────
@router.get("/invoices/{invoice_id}/file")
async def get_invoice_file(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """Serve the original uploaded invoice file."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    
    file_path = UPLOAD_DIR / inv.filename
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")
        
    return FileResponse(file_path, media_type="application/pdf", content_disposition_type="inline")


# ── POST /invoices/{id}/reprocess ─────────────
@router.post("/invoices/{invoice_id}/reprocess", response_model=InvoiceResponse)
async def reprocess_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """Re-run the AI pipeline on an existing uploaded file."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")

    file_path = UPLOAD_DIR / inv.filename
    if not file_path.exists():
        raise HTTPException(400, f"Original file '{inv.filename}' not found on disk")

    await _log(db, inv.id, "Reprocessing triggered", user="human")
    try:
        await _run_pipeline(inv, file_path, db)
    except Exception as exc:
        logger.exception("Reprocess error for invoice %d", inv.id)
        await _log(db, inv.id, f"Reprocess error: {exc}")

    await db.refresh(inv)
    return _invoice_to_response(inv)


# ── PUT /invoices/{id} (backward compat) ──────
@router.put("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int, update: InvoiceUpdate, db: AsyncSession = Depends(get_db),
):
    """Manually correct extracted data and re-validate."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")

    current: dict = json.loads(inv.extracted_data) if inv.extracted_data else {}
    update_dict = update.model_dump(exclude_none=True, exclude={"reason"})
    for field, new_val in update_dict.items():
        old_val = current.get(field)
        if old_val != new_val:
            old_s = json.dumps(old_val) if isinstance(old_val, (list, dict)) else str(old_val)
            new_s = json.dumps(new_val) if isinstance(new_val, (list, dict)) else str(new_val)
            await _log(db, inv.id, f"Field '{field}': {old_s} → {new_s}", reason=update.reason, user="human")
            current[field] = new_val

    inv.extracted_data = json.dumps(current)
    invoice_data = InvoiceData(**current)
    validation = validate_invoice(invoice_data)
    inv.validation_result = json.dumps(validation.model_dump())

    inv.status = InvoiceStatus.APPROVED if validation.is_valid else InvoiceStatus.PENDING_REVIEW
    await db.commit()
    await db.refresh(inv)
    return _invoice_to_response(inv)


# ── GET /invoices/{id}/export ─────────────────
@router.get("/invoices/{invoice_id}/export")
async def export_invoice(
    invoice_id: int,
    format: str = Query("csv", pattern="^(csv|json|tally|erpnext)$"),
    db: AsyncSession = Depends(get_db),
):
    """Export invoice as CSV, JSON, Tally XML, or push to ERPNext. Only allowed from Approved status."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.status not in (InvoiceStatus.APPROVED, InvoiceStatus.POSTED):
        raise HTTPException(400, f"Export only allowed from 'Approved' or 'Posted' status (current: '{inv.status}'). Complete HITL review first.")
    if not inv.extracted_data:
        raise HTTPException(400, "No extracted data available")

    data = InvoiceData(**json.loads(inv.extracted_data))

    if format == "erpnext":
        result = export_to_erpnext(data)
        if result["success"]:
            inv.status = InvoiceStatus.POSTED
            await db.commit()
            await _log(db, inv.id, f"Pushed to ERPNext (Ref: {result.get('erp_reference')}) — status → Posted")
            return {"status": "success", "message": result["message"], "erp_reference": result.get("erp_reference")}
        else:
            raise HTTPException(500, f"Failed to push to ERPNext: {result.get('error')}")

    if format == "csv":
        content, filename = export_invoice_csv(invoice_id, data)
        mt = "text/csv"
    elif format == "tally":
        content, filename = export_invoice_tally(invoice_id, data)
        mt = "application/xml"
    else:
        content, filename = export_invoice_json(invoice_id, data)
        mt = "application/json"

    inv.status = InvoiceStatus.POSTED
    await db.commit()
    await _log(db, inv.id, f"Exported as {format.upper()} — status → Posted")
    return Response(
        content=content,
        media_type=mt,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET /invoices/{id}/audit-log ──────────────
@router.get("/invoices/{invoice_id}/audit-log", response_model=list[AuditLogResponse])
async def get_audit_log(invoice_id: int, db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.invoice_id == invoice_id).order_by(AuditLog.timestamp.asc())
    )).scalars().all()
    return [AuditLogResponse.model_validate(r) for r in rows]
