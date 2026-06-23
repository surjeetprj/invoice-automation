from __future__ import annotations

"""SQLAlchemy ORM models for the normalized desktop database."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all desktop ORM models."""


def utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class TallyMasterMapping(Base):
    """Confirmed mapping from invoice/config source values to local Tally masters."""

    __tablename__ = "tally_master_mapping"
    __table_args__ = (
        Index(
            "ix_tally_master_mapping_lookup",
            "biz_id",
            "company_name",
            "mapping_type",
            "source_value",
            "is_active",
        ),
    )

    mapping_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    biz_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mapping_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tally_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[str] = mapped_column(String(1), default="Y")
    created_dtm: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invoice(Base):
    """Persisted invoice file, workflow, and review summary."""

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

    extraction: Mapped["InvoiceExtraction | None"] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        uselist=False,
    )
    validation_issues: Mapped[list["InvoiceValidationIssue"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceValidationIssue.id",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceExtraction(Base):
    """Scalar extracted invoice data and raw extraction text."""

    __tablename__ = "invoice_extractions"
    __table_args__ = (UniqueConstraint("invoice_id", name="uq_invoice_extractions_invoice_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoices.id"), nullable=False)
    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    challan_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    challan_date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    e_way_bill_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    supply_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reverse_charge: Mapped[str | None] = mapped_column(String(50), nullable=True)
    irn: Mapped[str | None] = mapped_column(Text, nullable=True)
    ack_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ack_date: Mapped[str | None] = mapped_column(String(30), nullable=True)
    qr_code_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    vendor_state_code: Mapped[str | None] = mapped_column(String(5), nullable=True)
    vendor_pan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vendor_msme_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vendor_contact: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    customer_state_code: Mapped[str | None] = mapped_column(String(5), nullable=True)
    customer_pan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    place_of_supply: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipping_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipping_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipping_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    transport_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vehicle_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_taxable_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total_cgst: Mapped[float] = mapped_column(Float, default=0.0)
    total_sgst: Mapped[float] = mapped_column(Float, default=0.0)
    total_igst: Mapped[float] = mapped_column(Float, default=0.0)
    total_cess: Mapped[float] = mapped_column(Float, default=0.0)
    total_tax_amount: Mapped[float] = mapped_column(Float, default=0.0)
    round_off: Mapped[float] = mapped_column(Float, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    amount_in_words: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ifsc: Mapped[str | None] = mapped_column(String(30), nullable=True)
    branch: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)

    invoice: Mapped[Invoice] = relationship(back_populates="extraction")
    line_items: Mapped[list["InvoiceLineItem"]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.position",
    )
    tax_breakups: Mapped[list["InvoiceTaxBreakup"]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="InvoiceTaxBreakup.id",
    )


class InvoiceLineItem(Base):
    """Extracted invoice line item."""

    __tablename__ = "invoice_line_items"
    __table_args__ = (Index("ix_invoice_line_items_extraction_id", "extraction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    extraction_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoice_extractions.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sr_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    hsn_sac: Mapped[str | None] = mapped_column(String(30), nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    rate: Mapped[float] = mapped_column(Float, default=0.0)
    discount: Mapped[float] = mapped_column(Float, default=0.0)
    taxable_value: Mapped[float] = mapped_column(Float, default=0.0)
    cess_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total: Mapped[float] = mapped_column(Float, default=0.0)

    extraction: Mapped[InvoiceExtraction] = relationship(back_populates="line_items")
    taxes: Mapped[list["InvoiceLineTax"]] = relationship(
        back_populates="line_item",
        cascade="all, delete-orphan",
        order_by="InvoiceLineTax.id",
    )


class InvoiceLineTax(Base):
    """Tax component attached to one invoice line item."""

    __tablename__ = "invoice_line_taxes"
    __table_args__ = (Index("ix_invoice_line_taxes_line_item_id", "line_item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    line_item_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoice_line_items.id"), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(30), default="")
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)
    taxable_amount: Mapped[float] = mapped_column(Float, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)

    line_item: Mapped[InvoiceLineItem] = relationship(back_populates="taxes")


class InvoiceTaxBreakup(Base):
    """Invoice-level tax breakup row."""

    __tablename__ = "invoice_tax_breakups"
    __table_args__ = (Index("ix_invoice_tax_breakups_extraction_id", "extraction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    extraction_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoice_extractions.id"), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(30), default="")
    tax_rate: Mapped[float] = mapped_column(Float, default=0.0)
    taxable_amount: Mapped[float] = mapped_column(Float, default=0.0)
    tax_amount: Mapped[float] = mapped_column(Float, default=0.0)

    extraction: Mapped[InvoiceExtraction] = relationship(back_populates="tax_breakups")


class InvoiceValidationIssue(Base):
    """Persisted validation error or warning shown by the UI."""

    __tablename__ = "invoice_validation_issues"
    __table_args__ = (Index("ix_invoice_validation_issues_invoice_id", "invoice_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoices.id"), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    field: Mapped[str] = mapped_column(String(100), default="General")

    invoice: Mapped[Invoice] = relationship(back_populates="validation_issues")


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
