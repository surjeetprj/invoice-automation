from __future__ import annotations

"""SQLAlchemy ORM models for the desktop database."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all desktop ORM models."""


def utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class Invoice(Base):
    """Persisted invoice record and extracted processing state."""

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_invoice_number_extracted", "invoice_number_extracted"),
        Index("ix_invoices_vendor_gstin", "vendor_gstin"),
        Index("ix_invoices_created_at", "created_at"),
        Index("ix_invoices_file_hash", "file_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="New")
    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_number_extracted: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_date_extracted: Mapped[str | None] = mapped_column(String(30), nullable=True)
    total_amount_extracted: Mapped[float | None] = mapped_column(Float, nullable=True)
    vendor_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    supply_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=utcnow, nullable=True)

    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class AuditLog(Base):
    """Audit timeline entry associated with an invoice."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_invoice_id", "invoice_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoices.id"), nullable=False)
    user: Mapped[str] = mapped_column(String(100), default="system")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    invoice: Mapped[Invoice] = relationship(back_populates="audit_logs")
