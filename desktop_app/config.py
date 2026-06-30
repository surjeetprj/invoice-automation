from __future__ import annotations

"""Configuration and runtime paths for the desktop application."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


APP_DIR = Path(__file__).resolve().parent
APP_NAME = "BahiAI"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"


def executable_dir() -> Path:
    """Return the directory that owns the frozen executable or source package."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return APP_DIR


def default_app_data_dir() -> Path:
    """Return the standard app-data directory for this platform."""
    if sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def is_portable_mode() -> bool:
    """Return True when runtime data should live beside the executable."""
    exe_dir = executable_dir()
    return (exe_dir / "portable.txt").exists() or (exe_dir / "data").exists()


def app_env_path() -> Path:
    """Return the highest-priority .env path for the current runtime context."""
    exe_env = executable_dir() / ".env"
    if exe_env.exists():
        return exe_env
    appdata_env = default_app_data_dir() / ".env"
    if appdata_env.exists() or getattr(sys, "frozen", False):
        return appdata_env
    return APP_DIR / ".env"


def ensure_appdata_env_template() -> None:
    """Create a customer-editable AppData .env template when absent."""
    path = default_app_data_dir() / ".env"
    if path.exists() or is_portable_mode():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# BahiAI runtime configuration",
                "GOOGLE_API_KEY=",
                f"GEMINI_MODEL={DEFAULT_GEMINI_MODEL}",
                "TALLY_URL=http://localhost:9000",
                "TALLY_COMPANY=",
                "TALLY_TIMEOUT_SECONDS=20",
                "",
            ]
        ),
        encoding="utf-8",
    )


def load_runtime_env() -> Path:
    """Load the prioritized .env file and return the chosen path."""
    if getattr(sys, "frozen", False):
        ensure_appdata_env_template()
    path = app_env_path()
    load_dotenv(path, override=True)
    return path


ENV_PATH = load_runtime_env()


def app_data_dir() -> Path:
    """Return a platform-appropriate writable runtime directory."""
    override = os.getenv("DESKTOP_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    if is_portable_mode():
        return executable_dir() / "data"
    return default_app_data_dir()


RUNTIME_DIR = app_data_dir()
UPLOAD_DIR = RUNTIME_DIR / "uploads"
EXPORT_DIR = RUNTIME_DIR / "exports"
LOG_DIR = RUNTIME_DIR / "logs"


def ensure_runtime_dirs() -> None:
    """Create runtime directories when the app is starting or writing data."""
    for directory in (RUNTIME_DIR, UPLOAD_DIR, EXPORT_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


DATABASE_URL = os.getenv("DESKTOP_DATABASE_URL", f"sqlite:///{(RUNTIME_DIR / 'bahiai.db').as_posix()}")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
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


def get_gemini_config() -> tuple[str, str]:
    """Return the latest Gemini API key and model from the prioritized .env."""
    values = dotenv_values(app_env_path())
    api_key = values.get("GOOGLE_API_KEY")
    model = values.get("GEMINI_MODEL")
    return str(api_key if api_key is not None else os.getenv("GOOGLE_API_KEY", "")), str(
        model if model is not None else os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    )


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
