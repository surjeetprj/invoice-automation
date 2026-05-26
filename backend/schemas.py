"""
Pydantic schemas for request/response validation.

Production-ready schemas for Indian GST Tax Invoices with support for:
- CGST + SGST (intra-state) and IGST (inter-state) tax handling
- E-invoicing (IRN, QR code)
- Human-in-the-Loop (HITL) review workflow
- Field-level confidence scoring
- Tally ERP export compatibility
"""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, model_validator



# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────
class SupplyType(str, Enum):
    INTRA_STATE = "INTRA_STATE"   # Same state → CGST + SGST
    INTER_STATE = "INTER_STATE"   # Different state → IGST
    UNKNOWN = "UNKNOWN"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    APPROVE_WITH_CORRECTIONS = "approve_with_corrections"
    REJECT = "reject"


# ──────────────────────────────────────────────
# Tax Detail (per-tax-type breakdown)
# ──────────────────────────────────────────────
class TaxDetail(BaseModel):
    """Represents a single tax component (e.g., CGST @ 9%)."""
    tax_type: str = ""              # CGST, SGST, IGST, CESS, etc.
    tax_rate: float = 0.0           # Percentage, e.g., 9.0 for 9%
    taxable_amount: float = 0.0     # Base amount on which this tax is calculated
    tax_amount: float = 0.0         # Calculated tax amount


# ──────────────────────────────────────────────
# Line Item
# ──────────────────────────────────────────────
class LineItem(BaseModel):
    """A single line item from the invoice table."""
    sr_no: int | None = None
    description: str = ""
    hsn_sac: str | None = None          # HSN / SAC code
    quantity: float = 0.0
    unit: str | None = None             # NOS, KG, PCS, etc.
    rate: float = 0.0                   # Unit price
    discount: float = 0.0              # Discount amount on this line
    taxable_value: float = 0.0          # (qty × rate) - discount (before tax)
    taxes: list[TaxDetail] = Field(default_factory=list)  # Per-item tax breakdown
    cess_amount: float = 0.0           # CESS amount if applicable
    total: float = 0.0                  # taxable_value + sum(tax amounts) + cess


# ──────────────────────────────────────────────
# Extracted invoice data (full structure)
# ──────────────────────────────────────────────
class InvoiceData(BaseModel):
    """Complete structured representation of a GST Tax Invoice."""

    # ── Invoice header ────────────────────────
    invoice_number: str | None = None
    date: str | None = None
    due_date: str | None = None
    challan_no: str | None = None
    challan_date: str | None = None
    e_way_bill_no: str | None = None
    supply_type: SupplyType = SupplyType.UNKNOWN
    reverse_charge: str | None = None       # "Y" or "N"

    # ── E-invoicing ──────────────────────────
    irn: str | None = None                  # Invoice Reference Number
    ack_number: str | None = None           # E-invoice acknowledgement number
    ack_date: str | None = None             # E-invoice acknowledgement date
    qr_code_data: str | None = None         # QR code content (if decoded)

    # ── Vendor (seller) details ───────────────
    vendor_name: str | None = None
    vendor_address: str | None = None
    vendor_gstin: str | None = None
    vendor_state_code: str | None = None    # First 2 digits of GSTIN
    vendor_pan: str | None = None           # Chars 3–12 of GSTIN
    vendor_msme_no: str | None = None
    vendor_contact: str | None = None       # Phone / email

    # ── Customer (buyer / Bill-To) ────────────
    customer_name: str | None = None
    customer_address: str | None = None
    customer_gstin: str | None = None
    customer_state_code: str | None = None  # First 2 digits of GSTIN
    customer_pan: str | None = None
    customer_phone: str | None = None
    place_of_supply: str | None = None      # State + code, e.g. "Maharashtra (27)"

    # ── Shipping (Ship-To) ───────────────────
    shipping_name: str | None = None
    shipping_address: str | None = None
    shipping_gstin: str | None = None

    # ── Transport details ─────────────────────
    transport_name: str | None = None
    transport_id: str | None = None
    vehicle_number: str | None = None

    # ── Line items ────────────────────────────
    line_items: list[LineItem] = Field(default_factory=list)

    # ── Tax summary ───────────────────────────
    total_taxable_amount: float = 0.0       # Sum of all taxable_values
    tax_breakup: list[TaxDetail] = Field(default_factory=list)  # Aggregated taxes
    total_cgst: float = 0.0                 # Total CGST amount
    total_sgst: float = 0.0                 # Total SGST amount
    total_igst: float = 0.0                 # Total IGST amount
    total_cess: float = 0.0                 # Total CESS amount
    total_tax_amount: float = 0.0           # Sum of all tax amounts
    round_off: float = 0.0                  # Round-off adjustment (±₹1)
    total_amount: float = 0.0               # Grand total (taxable + tax + round_off)
    amount_in_words: str | None = None

    # ── Bank details ──────────────────────────
    bank_name: str | None = None
    account_no: str | None = None
    ifsc: str | None = None
    branch: str | None = None

    # ── AI Metadata ───────────────────────────
    confidence_score: float = 0.0           # Overall extraction confidence (0.0–1.0)


# ──────────────────────────────────────────────
# Field-level confidence (for HITL review UI)
# ──────────────────────────────────────────────
class FieldConfidence(BaseModel):
    """Confidence score for a specific extracted field."""
    field_name: str
    value: str | None = None
    confidence: float = 0.0     # 0.0 = no confidence, 1.0 = fully confident
    needs_review: bool = False  # True if below MIN_CONFIDENCE_SCORE


# ──────────────────────────────────────────────
# Validation result
# ──────────────────────────────────────────────
class ValidationIssue(BaseModel):
    severity: str  # "error" or "warning"
    message: str
    field: str = "General"

class ValidationResult(BaseModel):
    is_valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    field_confidences: list[FieldConfidence] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def populate_issues(cls, data):
        if isinstance(data, dict):
            errors = data.get("errors", [])
            warnings = data.get("warnings", [])
            issues = data.get("issues")
            if issues is None or len(issues) == 0:
                issues_list = []
                for err in errors:
                    field = "General"
                    err_lower = err.lower()
                    if "gstin" in err_lower:
                        field = "GSTIN"
                    elif "date" in err_lower:
                        field = "Date"
                    elif "invoice_number" in err_lower or "invoice #" in err_lower:
                        field = "Invoice Number"
                    elif any(k in err_lower for k in ["amount", "total", "taxable", "cgst", "sgst", "igst", "cess", "math"]):
                        field = "Calculation"
                    elif "line" in err_lower or "item" in err_lower:
                        field = "Line Items"
                    issues_list.append({"severity": "error", "message": err, "field": field})
                
                for warn in warnings:
                    field = "General"
                    warn_lower = warn.lower()
                    if "gstin" in warn_lower:
                        field = "GSTIN"
                    elif "date" in warn_lower:
                        field = "Date"
                    elif "reverse charge" in warn_lower:
                        field = "Reverse Charge"
                    elif "place of supply" in warn_lower:
                        field = "Place of Supply"
                    elif "e-way bill" in warn_lower:
                        field = "E-Way Bill"
                    elif "hsn" in warn_lower or "sac" in warn_lower:
                        field = "HSN/SAC Code"
                    elif any(k in warn_lower for k in ["amount", "total", "taxable", "cgst", "sgst", "igst", "cess", "math"]):
                        field = "Calculation"
                    elif "line" in warn_lower or "item" in warn_lower:
                        field = "Line Items"
                    issues_list.append({"severity": "warning", "message": warn, "field": field})
                
                data["issues"] = issues_list
        return data




# ──────────────────────────────────────────────
# API responses
# ──────────────────────────────────────────────
class InvoiceResponse(BaseModel):
    id: int
    filename: str
    status: str
    supply_type: str | None = None
    confidence_score: float | None = None
    raw_markdown: str | None = None
    extracted_data: InvoiceData | None = None
    validation: ValidationResult | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    processing_time_ms: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    total: int
    invoices: list[InvoiceResponse]


# ──────────────────────────────────────────────
# Human-in-the-loop: Review request
# ──────────────────────────────────────────────
class InvoiceReviewRequest(BaseModel):
    """Payload for human reviewer to approve, correct, or reject an invoice."""
    decision: ReviewDecision
    corrections: dict | None = None           # field: value pairs for corrections
    rejection_reason: str | None = None       # required when decision is 'reject'
    reviewer: str = "reviewer"                # reviewer identity


class InvoiceReviewResponse(BaseModel):
    """Response after processing a review decision."""
    id: int
    status: str
    decision: str
    message: str
    validation: ValidationResult | None = None


# ──────────────────────────────────────────────
# Human-in-the-loop: Update schema (backward compat)
# ──────────────────────────────────────────────
class InvoiceUpdate(BaseModel):
    """Fields a user can manually correct after extraction."""
    invoice_number: str | None = None
    date: str | None = None
    challan_no: str | None = None
    challan_date: str | None = None
    e_way_bill_no: str | None = None

    vendor_name: str | None = None
    vendor_address: str | None = None
    vendor_gstin: str | None = None

    customer_name: str | None = None
    customer_address: str | None = None
    customer_gstin: str | None = None
    customer_phone: str | None = None
    place_of_supply: str | None = None

    shipping_name: str | None = None
    shipping_address: str | None = None
    shipping_gstin: str | None = None

    line_items: list[LineItem] | None = None
    tax_breakup: list[TaxDetail] | None = None

    total_taxable_amount: float | None = None
    total_cgst: float | None = None
    total_sgst: float | None = None
    total_igst: float | None = None
    total_cess: float | None = None
    total_tax_amount: float | None = None
    round_off: float | None = None
    total_amount: float | None = None

    reverse_charge: str | None = None
    supply_type: SupplyType | None = None

    bank_name: str | None = None
    account_no: str | None = None
    ifsc: str | None = None
    branch: str | None = None

    reason: str = "Manual correction"


# ──────────────────────────────────────────────
# Dashboard stats
# ──────────────────────────────────────────────
class DashboardStats(BaseModel):
    total_invoices: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    status_distribution: dict[str, int] = Field(default_factory=dict)
    avg_processing_time_ms: float | None = None
    avg_confidence_score: float | None = None
    total_pending_review: int = 0
    total_approved: int = 0
    total_rejected: int = 0



# ──────────────────────────────────────────────
# Batch processing
# ──────────────────────────────────────────────
class BatchProcessResult(BaseModel):
    filename: str
    invoice_id: int | None = None
    status: str
    error: str | None = None


class BatchProcessResponse(BaseModel):
    total_files: int
    successful: int
    failed: int
    results: list[BatchProcessResult]


# ──────────────────────────────────────────────
# Audit log response
# ──────────────────────────────────────────────
class AuditLogResponse(BaseModel):
    id: int
    invoice_id: int
    user: str
    action: str
    reason: str | None = None
    timestamp: datetime | None = None

    model_config = {"from_attributes": True}
