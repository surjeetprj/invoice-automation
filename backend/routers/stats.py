from fastapi import APIRouter, Depends
from sqlalchemy import select, func as sa_func, and_, or_, exists
from sqlalchemy.ext.asyncio import AsyncSession

from config import InvoiceStatus
from database import get_db
from models import AuditLog, Invoice
from schemas import DashboardStats

from routers.auth import verify_api_key

router = APIRouter(tags=["Stats & Health"])

# ── GET /stats ────────────────────────────────
@router.get("/stats", response_model=DashboardStats)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(verify_api_key),
):
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
@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Invoice Automation", "version": "2.0.0"}
