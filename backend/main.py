"""
Invoice Automation — FastAPI Application (Production)

Status Flow: New → In_Process → Extracted → Pending_Review → Approved/Rejected → Posted

Routes:
    POST   /process-invoice              Upload & process single invoice
    POST   /process-invoices/batch       Upload & process multiple invoices
    GET    /invoices                     List all invoices (paginated)
    GET    /invoices/{id}                Get single invoice detail
    PUT    /invoices/{id}                Human correction (backward compat)
    GET    /invoices/{id}/review         Get invoice for HITL review
    POST   /invoices/{id}/review         Submit review decision
    POST   /invoices/{id}/reprocess      Re-run AI pipeline
    GET    /invoices/{id}/export         Export to CSV, JSON, or Tally XML
    GET    /invoices/{id}/audit-log      View audit trail
    GET    /stats                        Dashboard statistics
    GET    /health                       Health check
"""
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func as sa_func, and_, or_, exists
from sqlalchemy.ext.asyncio import AsyncSession


from config import (
    ALLOWED_EXTENSIONS, API_KEY, UPLOAD_DIR, DUPLICATE_CHECK_ENABLED,
    MAX_FILE_SIZE_MB, InvoiceStatus,
)
from database import get_db, init_db
from models import AuditLog, Invoice
from schemas import (
    AuditLogResponse, BatchProcessResponse, BatchProcessResult,
    DashboardStats, InvoiceData, InvoiceListResponse, InvoiceResponse,
    InvoiceReviewRequest, InvoiceReviewResponse, InvoiceUpdate,
    ValidationResult,
)
from services.exporter import export_invoice_csv, export_invoice_json
from services.tally_exporter import export_invoice_tally
from services.erpnext_exporter import export_to_erpnext
from services.doc_extraction_engine import extract_with_metadata, initialize_ocr, ScannedDocumentException
from services.ai_parser import parse_invoice
from services.validator import validate_invoice, calculate_confidence_score
from services.reviewer import apply_review_decision, get_review_payload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress /health endpoint logs in terminal
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())


# ── Lifespan ──────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting Invoice Automation…")
    await init_db()
    logger.info("✅ Database initialised.")
    initialize_ocr()
    yield
    logger.info("👋 Shutting down.")


app = FastAPI(
    title="Invoice Automation",
    description="Production invoice processing: Docling → AI Parse → Validate → HITL Review → Export",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/docs")


# ── Auth ──────────────────────────────────────
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

async def verify_api_key(api_key: str = Security(_api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key


# ── Helpers ───────────────────────────────────
def _invoice_to_response(inv: Invoice) -> InvoiceResponse:
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


async def _log(db, invoice_id, action, reason=None, user="system"):
    db.add(AuditLog(invoice_id=invoice_id, user=user, action=action, reason=reason))
    await db.commit()


async def _run_pipeline(invoice: Invoice, file_path: Path, db: AsyncSession):
    """Core pipeline: Docling → Parse → Validate → Pending_Review."""
    start = time.perf_counter()

    # Step 1: In_Process
    invoice.status = InvoiceStatus.IN_PROCESS
    await db.commit()
    await _log(db, invoice.id, "Status → In_Process")

    # Step 2: Docling extraction
    raw_markdown, meta = extract_with_metadata(str(file_path))
    invoice.raw_markdown = raw_markdown
    await db.commit()
    quality = f"{meta.character_count} chars, {meta.page_count} pages, {meta.table_count} tables"
    await _log(db, invoice.id, f"Docling extraction complete — {quality}")

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


# ══════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════

# ── POST /process-invoice ─────────────────────
@app.post("/process-invoice", response_model=InvoiceResponse, status_code=201)
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
        # We check after extraction, but warn on filename
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
@app.post("/process-invoices/batch", response_model=BatchProcessResponse, status_code=201)
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
@app.get("/invoices", response_model=InvoiceListResponse)
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
@app.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(invoice_id: int, db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return _invoice_to_response(inv)


# ── GET /invoices/{id}/review ─────────────────
@app.get("/invoices/{invoice_id}/review")
async def get_invoice_review(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """Get invoice data for HITL review (side-by-side: raw markdown + extracted data + validation)."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return get_review_payload(inv)


# ── POST /invoices/{id}/review ────────────────
@app.post("/invoices/{invoice_id}/review", response_model=InvoiceReviewResponse)
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


# ── POST /invoices/{id}/reprocess ─────────────
@app.get("/invoices/{invoice_id}/file", tags=["Invoices"])
async def get_invoice_file(invoice_id: int, db: AsyncSession = Depends(get_db)):
    """Serve the original uploaded invoice file."""
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    
    file_path = UPLOAD_DIR / inv.filename
    if not file_path.exists():
        raise HTTPException(404, "File not found on disk")
        
    return FileResponse(file_path)

@app.post("/invoices/{invoice_id}/reprocess", response_model=InvoiceResponse, tags=["Invoices"])
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
@app.put("/invoices/{invoice_id}", response_model=InvoiceResponse)
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
@app.get("/invoices/{invoice_id}/export")
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
@app.get("/invoices/{invoice_id}/audit-log", response_model=list[AuditLogResponse])
async def get_audit_log(invoice_id: int, db: AsyncSession = Depends(get_db)):
    inv = (await db.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.invoice_id == invoice_id).order_by(AuditLog.timestamp.asc())
    )).scalars().all()
    return [AuditLogResponse.model_validate(r) for r in rows]


# ── GET /stats ────────────────────────────────
@app.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    """Dashboard statistics."""
    total = (await db.execute(select(sa_func.count(Invoice.id)))).scalar() or 0
    avg_time = (await db.execute(select(sa_func.avg(Invoice.processing_time_ms)))).scalar()
    avg_conf = (await db.execute(select(sa_func.avg(Invoice.confidence_score)))).scalar()

    by_status = {}
    for status_val in InvoiceStatus.ALL:
        cnt = (await db.execute(
            select(sa_func.count(Invoice.id)).where(Invoice.status == status_val)
        )).scalar() or 0
        if cnt > 0:
            by_status[status_val] = cnt

    # Calculate total approved/posted invoices without corrections
    corr_exists = exists().where(
        and_(
            AuditLog.invoice_id == Invoice.id,
            or_(
                AuditLog.action.like("%APPROVED WITH CORRECTIONS%"),
                AuditLog.action.like("%corrected%")
            )
        )
    )
    
    no_corr_stmt = select(sa_func.count(Invoice.id)).where(
        and_(
            Invoice.status.in_([InvoiceStatus.APPROVED, InvoiceStatus.POSTED]),
            ~corr_exists
        )
    )
    total_approved_no_corrections = (await db.execute(no_corr_stmt)).scalar() or 0

    return DashboardStats(
        total_invoices=total, by_status=by_status,
        status_distribution=by_status,
        avg_processing_time_ms=round(avg_time, 1) if avg_time else None,
        avg_confidence_score=round(avg_conf, 2) if avg_conf else None,
        total_pending_review=by_status.get(InvoiceStatus.PENDING_REVIEW, 0),
        total_approved=total_approved_no_corrections,
        total_rejected=by_status.get(InvoiceStatus.REJECTED, 0),
    )



# ── Health check ──────────────────────────────
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Invoice Automation", "version": "2.0.0"}
