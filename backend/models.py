"""
SQLAlchemy ORM models for Invoice and AuditLog.

Production schema with HITL review tracking, duplicate detection,
and extraction quality metrics.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="New")

    # ── Raw extraction ────────────────────────
    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    validation_result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string

    # ── Denormalized fields for queries/duplicate detection ──
    invoice_number_extracted: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vendor_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    supply_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Extraction quality ────────────────────
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── HITL review tracking ──────────────────
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=_utcnow, nullable=True
    )

    # Relationship
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<Invoice id={self.id} filename='{self.filename}' status='{self.status}'>"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoices.id"), nullable=False)
    user: Mapped[str] = mapped_column(String(100), default="system")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationship
    invoice: Mapped["Invoice"] = relationship(back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog id={self.id} invoice_id={self.invoice_id} action='{self.action[:40]}'>"
