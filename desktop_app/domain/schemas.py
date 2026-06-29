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
    SAVE_CORRECTIONS = "save_corrections"
    APPROVE_WITH_CORRECTIONS = "approve_with_corrections"
    REJECT = "reject"


class TaxDetail(BaseModel):
    """A single tax component such as CGST, SGST, IGST, or CESS."""

    tax_type: str = Field(
        default="",
        description="GST component name exactly as visible, such as CGST, SGST, IGST, or CESS.",
    )
    tax_rate: float = Field(default=0.0, description="Visible tax rate percentage for this component.")
    taxable_amount: float = Field(
        default=0.0,
        description="Taxable base amount to which this tax component applies.",
    )
    tax_amount: float = Field(default=0.0, description="Tax amount for this component.")


class LineItem(BaseModel):
    """A single invoice product or service row."""

    sr_no: int | None = Field(default=None, description="Visible row serial number, if present.")
    item_name: str | None = Field(
        default=None,
        description=(
            "Short clean product or service name for ERP/Tally item masters. "
            "Do not include HSN/SAC, serial numbers, usernames, IP addresses, service periods, or remarks."
        ),
    )
    description: str | None = Field(
        default="",
        description=(
            "Optional product or service detail text for this row. "
            "Preserve any multi-line formatting (using newlines) if the description spans multiple lines. "
            "Do not repeat the item name at the beginning of the description if it is redundant."
        ),
    )
    hsn_sac: str | None = Field(default=None, description="Visible HSN or SAC code for this row.")
    quantity: float = Field(
        default=0.0,
        description="Visible quantity only when it can be read reliably; do not guess from totals.",
    )
    unit: str | None = Field(default=None, description="Visible unit of measure, if present.")
    rate: float = Field(
        default=0.0,
        description="Visible unit rate only when it can be read reliably; do not infer from unclear rows.",
    )
    discount: float = Field(default=0.0, description="Visible row discount amount, or 0 when absent.")
    taxable_value: float = Field(
        default=0.0,
        description="Row taxable value after discount, before GST or cess.",
    )
    taxes: list[TaxDetail] = Field(
        default_factory=list,
        description="GST components for this row, preserving CGST, SGST, IGST, and CESS rates and amounts.",
    )
    cess_amount: float = Field(default=0.0, description="Visible cess amount for this row, or 0 when absent.")
    total: float = Field(default=0.0, description="Visible row total including taxes when present.")


class InvoiceData(BaseModel):
    """Complete structured invoice extraction payload."""

    invoice_number: str | None = Field(default=None, description="Invoice number exactly as printed.")
    date: str | None = Field(default=None, description="Invoice date in DD-MM-YYYY format.")
    due_date: str | None = Field(
        default=None,
        description=(
            "Payment due date from labels such as Due Date, Payment Due Date, Valid Upto, "
            "or Valid Up To when a concrete date is visible. Use DD-MM-YYYY."
        ),
    )
    challan_no: str | None = Field(default=None, description="Delivery challan number, if visible.")
    challan_date: str | None = Field(default=None, description="Delivery challan date in DD-MM-YYYY format.")
    e_way_bill_no: str | None = Field(default=None, description="E-way bill number, if visible.")
    supply_type: SupplyType = Field(
        default=SupplyType.UNKNOWN,
        description="GST supply type derived from vendor and customer GSTIN state codes when possible.",
    )
    reverse_charge: str | None = Field(default=None, description="Reverse charge value or flag exactly as visible.")
    irn: str | None = Field(default=None, description="Invoice Reference Number from e-invoice details.")
    ack_number: str | None = Field(default=None, description="E-invoice acknowledgement number, if visible.")
    ack_date: str | None = Field(default=None, description="E-invoice acknowledgement date in DD-MM-YYYY format.")
    qr_code_data: str | None = Field(default=None, description="QR code text or decoded e-invoice data, if available.")
    vendor_name: str | None = Field(default=None, description="Supplier or seller legal/company name.")
    vendor_address: str | None = Field(default=None, description="Supplier or seller address.")
    vendor_gstin: str | None = Field(default=None, description="Supplier GSTIN.")
    vendor_state_code: str | None = Field(default=None, description="First two digits of supplier GSTIN when present.")
    vendor_pan: str | None = Field(default=None, description="Supplier PAN derived from GSTIN or printed PAN.")
    vendor_msme_no: str | None = Field(default=None, description="Supplier MSME/Udyam number, if visible.")
    vendor_contact: str | None = Field(default=None, description="Supplier phone, email, or contact details.")
    customer_name: str | None = Field(
        default=None,
        description="Billing customer legal/company name from Bill To/Billed To/Customer section.",
    )
    customer_address: str | None = Field(
        default=None,
        description="Billing customer address from Bill To/Billed To section; keep separate from Ship To.",
    )
    customer_gstin: str | None = Field(default=None, description="Billing customer GSTIN.")
    customer_state_code: str | None = Field(default=None, description="First two digits of customer GSTIN when present.")
    customer_pan: str | None = Field(default=None, description="Customer PAN derived from GSTIN or printed PAN.")
    customer_phone: str | None = Field(default=None, description="Billing customer phone or contact number.")
    place_of_supply: str | None = Field(default=None, description="Place/state of supply as printed or derived.")
    shipping_name: str | None = Field(
        default=None,
        description=(
            "Company/legal name from Ship To, Shipped To, Delivery To, or Consignee section. "
            "Do not copy Bill To unless no separate shipping section exists."
        ),
    )
    shipping_address: str | None = Field(
        default=None,
        description=(
            "Full address from Ship To, Shipped To, Delivery To, or Consignee section, excluding labels and GSTIN. "
            "Keep separate from billing customer address."
        ),
    )
    shipping_gstin: str | None = Field(
        default=None,
        description="GSTIN from Ship To, Shipped To, Delivery To, or Consignee section when visible.",
    )
    transport_name: str | None = Field(default=None, description="Transporter name, if visible.")
    transport_id: str | None = Field(default=None, description="Transporter ID or GSTIN, if visible.")
    vehicle_number: str | None = Field(default=None, description="Vehicle number, if visible.")
    line_items: list[LineItem] = Field(
        default_factory=list,
        description=(
            "Complete visible invoice rows only. For scanned/image invoices, avoid guessing unreadable "
            "quantity, rate, or discount; use one summary line when detailed rows are unreliable."
        ),
    )
    total_taxable_amount: float = Field(
        default=0.0,
        description="Invoice-level taxable subtotal from the totals section before GST and cess.",
    )
    tax_breakup: list[TaxDetail] = Field(
        default_factory=list,
        description="Invoice-level GST breakup preserving CGST, SGST, IGST, and CESS components.",
    )
    total_cgst: float = Field(default=0.0, description="Invoice-level total CGST amount.")
    total_sgst: float = Field(default=0.0, description="Invoice-level total SGST amount.")
    total_igst: float = Field(default=0.0, description="Invoice-level total IGST amount.")
    total_cess: float = Field(default=0.0, description="Invoice-level total cess amount.")
    total_tax_amount: float = Field(
        default=0.0,
        description="Invoice-level total tax amount, normally CGST plus SGST plus IGST plus cess.",
    )
    round_off: float = Field(default=0.0, description="Invoice round-off adjustment, positive or negative.")
    total_amount: float = Field(
        default=0.0,
        description="Final invoice grand total payable from the totals section.",
    )
    amount_in_words: str | None = Field(default=None, description="Grand total amount in words, if visible.")
    bank_name: str | None = Field(default=None, description="Vendor bank name, if visible.")
    account_no: str | None = Field(default=None, description="Vendor bank account number, if visible.")
    ifsc: str | None = Field(default=None, description="Vendor bank IFSC code, if visible.")
    branch: str | None = Field(default=None, description="Vendor bank branch, if visible.")
    confidence_score: float = Field(
        default=0.0,
        description="Overall extraction confidence from 0.0 to 1.0, where 1.0 means all key fields were clear.",
    )


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



class TallyMappingRecord(BaseModel):
    """Editable mapping from an invoice/config source value to a Tally master."""

    mapping_type: str
    source_value: str
    company_name: str | None = None
    tally_value: str | None = None
    is_active: str = "Y"
    candidates: list[str] = Field(default_factory=list)
    match_score: float | None = None
    auto_matched: bool = False

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
    ai_call_count: int = 0
    reprocess_count: int = 0
    rejection_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tally_mappings: list[TallyMappingRecord] = Field(default_factory=list)


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
    usage_from_date: str | None = None
    total_usage_count: int = 0
    ai_calls_since_date: int = 0
    reprocesses_since_date: int = 0


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
