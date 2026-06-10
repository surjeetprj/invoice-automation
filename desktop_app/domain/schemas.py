from __future__ import annotations

"""Pydantic data models used by the self-contained desktop workflow."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class SupplyType(str, Enum):
    """GST supply type derived from vendor/customer state codes."""

    INTRA_STATE = "INTRA_STATE"
    INTER_STATE = "INTER_STATE"
    UNKNOWN = "UNKNOWN"


class ReviewDecision(str, Enum):
    """Supported human review decisions."""

    APPROVE = "approve"
    APPROVE_WITH_CORRECTIONS = "approve_with_corrections"
    REJECT = "reject"


class TaxDetail(BaseModel):
    """A single tax component such as CGST, SGST, IGST, or CESS."""

    tax_type: str = ""
    tax_rate: float = 0.0
    taxable_amount: float = 0.0
    tax_amount: float = 0.0


class LineItem(BaseModel):
    """A single invoice product or service row."""

    sr_no: int | None = None
    description: str = ""
    hsn_sac: str | None = None
    quantity: float = 0.0
    unit: str | None = None
    rate: float = 0.0
    discount: float = 0.0
    taxable_value: float = 0.0
    taxes: list[TaxDetail] = Field(default_factory=list)
    cess_amount: float = 0.0
    total: float = 0.0


class InvoiceData(BaseModel):
    """Complete structured invoice extraction payload."""

    invoice_number: str | None = None
    date: str | None = None
    due_date: str | None = None
    challan_no: str | None = None
    challan_date: str | None = None
    e_way_bill_no: str | None = None
    supply_type: SupplyType = SupplyType.UNKNOWN
    reverse_charge: str | None = None
    irn: str | None = None
    ack_number: str | None = None
    ack_date: str | None = None
    qr_code_data: str | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    vendor_gstin: str | None = None
    vendor_state_code: str | None = None
    vendor_pan: str | None = None
    vendor_msme_no: str | None = None
    vendor_contact: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    customer_gstin: str | None = None
    customer_state_code: str | None = None
    customer_pan: str | None = None
    customer_phone: str | None = None
    place_of_supply: str | None = None
    shipping_name: str | None = None
    shipping_address: str | None = None
    shipping_gstin: str | None = None
    transport_name: str | None = None
    transport_id: str | None = None
    vehicle_number: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    total_taxable_amount: float = 0.0
    tax_breakup: list[TaxDetail] = Field(default_factory=list)
    total_cgst: float = 0.0
    total_sgst: float = 0.0
    total_igst: float = 0.0
    total_cess: float = 0.0
    total_tax_amount: float = 0.0
    round_off: float = 0.0
    total_amount: float = 0.0
    amount_in_words: str | None = None
    bank_name: str | None = None
    account_no: str | None = None
    ifsc: str | None = None
    branch: str | None = None
    confidence_score: float = 0.0


class FieldConfidence(BaseModel):
    """Confidence metadata for one extracted field."""

    field_name: str
    value: str | None = None
    confidence: float = 0.0
    needs_review: bool = False


class ValidationIssue(BaseModel):
    """A display-ready validation issue."""

    severity: str
    message: str
    field: str = "General"


class ValidationResult(BaseModel):
    """Validation result containing errors, warnings, and UI issues."""

    is_valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    field_confidences: list[FieldConfidence] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def populate_issues(cls, data):
        """Populate display issues from plain errors/warnings when needed."""
        if isinstance(data, dict) and not data.get("issues"):
            issues = []
            for message in data.get("errors", []):
                issues.append({"severity": "error", "message": message, "field": infer_issue_field(message)})
            for message in data.get("warnings", []):
                issues.append({"severity": "warning", "message": message, "field": infer_issue_field(message)})
            data["issues"] = issues
        return data


class InvoiceReviewRequest(BaseModel):
    """Payload submitted by the reviewer from the desktop UI."""

    decision: ReviewDecision
    corrections: dict | None = None
    rejection_reason: str | None = None
    reviewer: str = "reviewer"


class InvoiceRecord(BaseModel):
    """Display-ready invoice record returned to the desktop UI."""

    id: int
    filename: str
    file_path: str
    file_hash: str | None = None
    status: str
    supply_type: str | None = None
    confidence_score: float | None = None
    raw_markdown: str | None = None
    extracted_data: InvoiceData | None = None
    validation: ValidationResult | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    processing_time_ms: int | None = None
    rejection_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DashboardStats(BaseModel):
    """Dashboard KPI and status distribution payload."""

    total_invoices: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    status_distribution: dict[str, int] = Field(default_factory=dict)
    avg_processing_time_ms: float | None = None
    avg_confidence_score: float | None = None
    total_pending_review: int = 0
    total_approved: int = 0
    total_rejected: int = 0


class AuditLogRecord(BaseModel):
    """Display-ready audit log record."""

    id: int
    invoice_id: int
    user: str
    action: str
    reason: str | None = None
    timestamp: datetime | None = None


def infer_issue_field(message: str) -> str:
    """Infer a friendly validation field group from a message."""
    lower = message.lower()
    if "gstin" in lower:
        return "GSTIN"
    if "date" in lower:
        return "Date"
    if "invoice_number" in lower or "invoice number" in lower:
        return "Invoice Number"
    if any(key in lower for key in ["amount", "total", "taxable", "cgst", "sgst", "igst", "cess", "math"]):
        return "Calculation"
    if "line" in lower or "item" in lower:
        return "Line Items"
    if "e-way" in lower:
        return "E-Way Bill"
    if "place of supply" in lower:
        return "Place of Supply"
    return "General"
