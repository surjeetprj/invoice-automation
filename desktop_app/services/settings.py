from __future__ import annotations

"""Runtime-editable desktop settings stored outside the repository."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import config

SETTINGS_FILE = config.RUNTIME_DIR / "settings.json"


@dataclass(frozen=True)
class TallySettings:
    """Tally-related user-editable settings."""

    tally_url: str = config.TALLY_URL
    tally_company: str = config.TALLY_COMPANY
    invoiceai_license_file: str = config.INVOICEAI_LICENSE_FILE
    tally_timeout_seconds: int = config.TALLY_TIMEOUT_SECONDS
    tally_vendor_parent_ledger: str = config.TALLY_VENDOR_PARENT_LEDGER
    default_stock_group: str = config.DEFAULT_STOCK_GROUP
    purchase_ledger_name: str = config.PURCHASE_LEDGER_NAME
    input_cgst_ledger_name: str = config.INPUT_CGST_LEDGER_NAME
    input_sgst_ledger_name: str = config.INPUT_SGST_LEDGER_NAME
    input_igst_ledger_name: str = config.INPUT_IGST_LEDGER_NAME
    input_cess_ledger_name: str = config.INPUT_CESS_LEDGER_NAME

    def model_dump(self) -> dict[str, Any]:
        """Return a UI/API-friendly dictionary."""
        return asdict(self)


def get_tally_settings() -> TallySettings:
    """Load Tally settings from runtime JSON, falling back to .env/config defaults."""
    data = load_settings_file().get("tally", {})
    return build_tally_settings(data if isinstance(data, dict) else {})


def save_tally_settings(payload: dict[str, Any]) -> TallySettings:
    """Persist Tally settings to the runtime settings JSON file."""
    settings = build_tally_settings(payload)
    content = load_settings_file()
    content["tally"] = settings.model_dump()
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
    return settings


def load_settings_file() -> dict[str, Any]:
    """Return the runtime settings file content, or an empty object if absent."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def build_tally_settings(payload: dict[str, Any]) -> TallySettings:
    """Merge a partial payload with default Tally settings and normalize values."""
    defaults = TallySettings()
    merged = defaults.model_dump()
    for key in merged:
        if key in payload and payload[key] is not None:
            merged[key] = payload[key]
    merged["tally_timeout_seconds"] = positive_int(merged.get("tally_timeout_seconds"), defaults.tally_timeout_seconds)
    for key, value in list(merged.items()):
        if key != "tally_timeout_seconds":
            merged[key] = str(value or "").strip()
    return TallySettings(**merged)


def positive_int(value: Any, default: int) -> int:
    """Return a positive integer setting or the provided default."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def license_file_path(settings: TallySettings | None = None) -> str:
    """Return the active license path for Tally license checks."""
    return (settings or get_tally_settings()).invoiceai_license_file or config.INVOICEAI_LICENSE_FILE
