from __future__ import annotations

"""Configuration and runtime paths for the desktop application."""

import os
from pathlib import Path

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

RUNTIME_DIR = APP_DIR / "runtime"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
EXPORT_DIR = RUNTIME_DIR / "exports"
LOG_DIR = RUNTIME_DIR / "logs"

for directory in (RUNTIME_DIR, UPLOAD_DIR, EXPORT_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DESKTOP_DATABASE_URL", f"sqlite:///{RUNTIME_DIR / 'invoices.db'}")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ERPNEXT_URL = os.getenv("ERPNEXT_URL", "")
ERPNEXT_API_KEY = os.getenv("ERPNEXT_API_KEY", "")
ERPNEXT_API_SECRET = os.getenv("ERPNEXT_API_SECRET", "")

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
DUPLICATE_CHECK_ENABLED = os.getenv("DUPLICATE_CHECK_ENABLED", "true").lower() == "true"
MIN_CONFIDENCE_SCORE = float(os.getenv("MIN_CONFIDENCE_SCORE", "0.7"))
MATH_TOLERANCE = float(os.getenv("MATH_TOLERANCE", "2.0"))
EWAY_BILL_THRESHOLD = float(os.getenv("EWAY_BILL_THRESHOLD", "50000.0"))
VALID_GST_RATES = {0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0}
ALLOWED_EXTENSIONS = {".pdf"}


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
