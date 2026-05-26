"""
Application configuration and constants.

Production-ready configuration for Indian GST Sale Invoice Automation.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file
load_dotenv(Path(__file__).resolve().parent / ".env")

# ──────────────────────────────────────────────
# Directories
# ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"

# Auto-create runtime directories
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR / 'invoices_v3.db'}")

# ──────────────────────────────────────────────
# Auth — simple API-key authentication
# Override via environment variable for production.
# ──────────────────────────────────────────────
API_KEY = os.getenv("INVOICE_API_KEY", "poc-secret-key-change-me")

# ──────────────────────────────────────────────
# Google AI — Gemini
# ──────────────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# GCP Vertex AI Configuration (Production - Deprecated for Testing)
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")

# ──────────────────────────────────────────────
# ERPNext Configuration
# ──────────────────────────────────────────────
ERPNEXT_URL = os.getenv("ERPNEXT_URL", "http://localhost:8000")
ERPNEXT_API_KEY = os.getenv("ERPNEXT_API_KEY", "")
ERPNEXT_API_SECRET = os.getenv("ERPNEXT_API_SECRET", "")

# ──────────────────────────────────────────────
# Allowed upload extensions
# ──────────────────────────────────────────────
ALLOWED_EXTENSIONS = {".pdf"}


# ──────────────────────────────────────────────
# Indian GST Configuration
# ──────────────────────────────────────────────

# Valid GST rate slabs (%) — as per Indian GST law
VALID_GST_RATES: set[float] = {0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0}

# Math tolerance for validation (₹) — accounts for rounding in invoices
MATH_TOLERANCE: float = float(os.getenv("MATH_TOLERANCE", "2.0"))

# Maximum upload file size in MB
MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "25"))

# Duplicate invoice detection toggle
DUPLICATE_CHECK_ENABLED: bool = os.getenv("DUPLICATE_CHECK_ENABLED", "true").lower() == "true"

# Minimum confidence score to flag for priority HITL review
MIN_CONFIDENCE_SCORE: float = float(os.getenv("MIN_CONFIDENCE_SCORE", "0.7"))

# E-way bill threshold (₹) — required when invoice value exceeds this
EWAY_BILL_THRESHOLD: float = float(os.getenv("EWAY_BILL_THRESHOLD", "50000.0"))

# ──────────────────────────────────────────────
# Indian State Codes (first 2 digits of GSTIN)
# ──────────────────────────────────────────────
STATE_CODES: dict[str, str] = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
    "97": "Other Territory",  # for overseas transactions
}

# ──────────────────────────────────────────────
# Invoice Status Constants
# ──────────────────────────────────────────────
class InvoiceStatus:
    NEW = "New"
    IN_PROCESS = "In_Process"
    EXTRACTED = "Extracted"
    PENDING_REVIEW = "Pending_Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    POSTED = "Posted"

    ALL = {NEW, IN_PROCESS, EXTRACTED, PENDING_REVIEW, APPROVED, REJECTED, POSTED}
