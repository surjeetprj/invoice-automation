from __future__ import annotations

"""Configuration and runtime paths for the desktop application."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

APP_NAME = "InvoiceAI"


def app_data_dir() -> Path:
    """Return a platform-appropriate writable runtime directory."""
    override = os.getenv("DESKTOP_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


RUNTIME_DIR = app_data_dir()
UPLOAD_DIR = RUNTIME_DIR / "uploads"
EXPORT_DIR = RUNTIME_DIR / "exports"
LOG_DIR = RUNTIME_DIR / "logs"

for directory in (RUNTIME_DIR, UPLOAD_DIR, EXPORT_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DESKTOP_DATABASE_URL", f"sqlite:///{(RUNTIME_DIR / 'invoices.db').as_posix()}")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
PURCHASE_LEDGER_NAME = os.getenv("PURCHASE_LEDGER_NAME", "Purchase Account")
INPUT_CGST_LEDGER_NAME = os.getenv("INPUT_CGST_LEDGER_NAME", "Input CGST")
INPUT_SGST_LEDGER_NAME = os.getenv("INPUT_SGST_LEDGER_NAME", "Input SGST")
INPUT_IGST_LEDGER_NAME = os.getenv("INPUT_IGST_LEDGER_NAME", "Input IGST")
INPUT_CESS_LEDGER_NAME = os.getenv("INPUT_CESS_LEDGER_NAME", "Input CESS")
TALLY_URL = os.getenv("TALLY_URL", "http://localhost:9000")
TALLY_COMPANY = os.getenv("TALLY_COMPANY", "")
TALLY_TIMEOUT_SECONDS = int(os.getenv("TALLY_TIMEOUT_SECONDS", "20"))
TALLY_VENDOR_PARENT_LEDGER = os.getenv("TALLY_VENDOR_PARENT_LEDGER", "Sundry Creditors")
DEFAULT_STOCK_GROUP = os.getenv("DEFAULT_STOCK_GROUP", "Primary")

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
DUPLICATE_CHECK_ENABLED = os.getenv("DUPLICATE_CHECK_ENABLED", "true").lower() == "true"
PDF_TABLE_EXTRACTION_ENABLED = os.getenv("PDF_TABLE_EXTRACTION_ENABLED", "true").lower() == "true"
MIN_CONFIDENCE_SCORE = float(os.getenv("MIN_CONFIDENCE_SCORE", "0.7"))
MATH_TOLERANCE = float(os.getenv("MATH_TOLERANCE", "2.0"))
CURRENCY_DECIMAL_PLACES = int(os.getenv("CURRENCY_DECIMAL_PLACES", "2"))
EWAY_BILL_THRESHOLD = float(os.getenv("EWAY_BILL_THRESHOLD", "50000.0"))
VALID_GST_RATES = {0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0}
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


class InvoiceStatus:
    """String constants for invoice workflow states."""

    NEW = "New"
    IN_PROCESS = "In_Process"
    EXTRACTED = "Extracted"
    PENDING_REVIEW = "Pending_Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    POSTED = "Posted"

    ALL = {NEW, IN_PROCESS, EXTRACTED, PENDING_REVIEW, APPROVED, REJECTED, POSTED}


STATE_CODES = {
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
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}
