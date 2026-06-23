from __future__ import annotations

"""Runtime-editable desktop settings stored outside the repository."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .. import config

SETTINGS_FILE = config.RUNTIME_DIR / "settings.json"

GLOBAL_KEYS = {
    "tally_url",
    "invoiceai_license_file",
    "tally_timeout_seconds",
    "selected_company",
}
COMPANY_MAPPING_KEYS = {
    "tally_vendor_parent_ledger",
    "default_stock_group",
    "purchase_ledger_name",
    "input_cgst_ledger_name",
    "input_sgst_ledger_name",
    "input_igst_ledger_name",
    "input_cess_ledger_name",
}


@dataclass(frozen=True)
class TallySettings:
    """Tally-related user-editable settings for the selected company."""

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
    """Load global Tally settings plus config-backed mapping defaults."""
    return settings_from_document(normalized_settings_document(load_settings_file()))


def get_tally_settings_payload() -> dict[str, Any]:
    """Return global settings plus config defaults for the Settings dialog."""
    document = normalized_settings_document(load_settings_file())
    payload = settings_from_document(document).model_dump()
    payload["selected_company"] = document["global"].get("selected_company", "")
    payload["global_settings"] = dict(document["global"])
    payload["default_company_mapping"] = default_company_mapping()
    payload["company_mappings"] = {}
    return payload


def save_tally_settings(payload: dict[str, Any]) -> TallySettings:
    """Persist global Tally settings only; master mappings are stored in SQL."""
    document = normalized_settings_document(load_settings_file())
    normalized_payload = payload if isinstance(payload, dict) else {}

    global_settings = dict(document["global"])
    for key in GLOBAL_KEYS:
        if key in normalized_payload and normalized_payload[key] is not None:
            global_settings[key] = normalized_payload[key]
    if "tally_company" in normalized_payload and "selected_company" not in normalized_payload:
        global_settings["selected_company"] = normalized_payload["tally_company"]
    global_settings = build_global_settings(global_settings)

    content = load_settings_file()
    content["tally"] = {"global": global_settings}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(content, indent=2, sort_keys=True), encoding="utf-8")
    return settings_from_document(content["tally"])


def load_settings_file() -> dict[str, Any]:
    """Return the runtime settings file content, or an empty object if absent."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalized_settings_document(content: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy and current settings shapes into one internal document."""
    raw_tally = content.get("tally", content) if isinstance(content, dict) else {}
    raw_tally = raw_tally if isinstance(raw_tally, dict) else {}
    if "global" in raw_tally or "companies" in raw_tally:
        raw_global = raw_tally.get("global", {}) if isinstance(raw_tally.get("global", {}), dict) else {}
        raw_companies = raw_tally.get("companies", {}) if isinstance(raw_tally.get("companies", {}), dict) else {}
        global_settings = build_global_settings(raw_global)
        companies = {
            str(name).strip(): build_company_mapping(mapping if isinstance(mapping, dict) else {})
            for name, mapping in raw_companies.items()
            if str(name).strip()
        }
        return {"global": global_settings, "companies": companies}

    selected_company = str(raw_tally.get("selected_company") or raw_tally.get("tally_company") or config.TALLY_COMPANY or "").strip()
    global_settings = build_global_settings({**raw_tally, "selected_company": selected_company})
    companies: dict[str, dict[str, str]] = {}
    legacy_mapping = {key: raw_tally[key] for key in COMPANY_MAPPING_KEYS if key in raw_tally}
    if selected_company and legacy_mapping:
        companies[selected_company] = build_company_mapping(legacy_mapping)
    return {"global": global_settings, "companies": companies}


def build_global_settings(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize global Tally connection settings."""
    timeout_default = config.TALLY_TIMEOUT_SECONDS
    selected_company = payload.get("selected_company", payload.get("tally_company", config.TALLY_COMPANY))
    return {
        "tally_url": str(payload.get("tally_url", config.TALLY_URL) or "").strip(),
        "invoiceai_license_file": str(payload.get("invoiceai_license_file", config.INVOICEAI_LICENSE_FILE) or "").strip(),
        "tally_timeout_seconds": positive_int(payload.get("tally_timeout_seconds"), timeout_default),
        "selected_company": str(selected_company or "").strip(),
    }


def build_company_mapping(payload: dict[str, Any]) -> dict[str, str]:
    """Normalize per-company ledger and stock group mapping settings."""
    defaults = default_company_mapping()
    values = dict(defaults)
    for key in COMPANY_MAPPING_KEYS:
        if key in payload and payload[key] is not None:
            values[key] = str(payload[key] or "").strip()
    return values


def default_company_mapping() -> dict[str, str]:
    """Return config-backed mapping defaults."""
    return {
        "tally_vendor_parent_ledger": config.TALLY_VENDOR_PARENT_LEDGER,
        "default_stock_group": config.DEFAULT_STOCK_GROUP,
        "purchase_ledger_name": config.PURCHASE_LEDGER_NAME,
        "input_cgst_ledger_name": config.INPUT_CGST_LEDGER_NAME,
        "input_sgst_ledger_name": config.INPUT_SGST_LEDGER_NAME,
        "input_igst_ledger_name": config.INPUT_IGST_LEDGER_NAME,
        "input_cess_ledger_name": config.INPUT_CESS_LEDGER_NAME,
    }


def settings_from_document(document: dict[str, Any]) -> TallySettings:
    """Build the public TallySettings facade from normalized global settings."""
    normalized = normalized_settings_document({"tally": document})
    global_settings = normalized["global"]
    selected_company = global_settings.get("selected_company", "")
    return TallySettings(
        tally_url=global_settings["tally_url"],
        tally_company=selected_company,
        invoiceai_license_file=global_settings["invoiceai_license_file"],
        tally_timeout_seconds=global_settings["tally_timeout_seconds"],
        **default_company_mapping(),
    )


def build_tally_settings(payload: dict[str, Any]) -> TallySettings:
    """Merge a partial payload with existing runtime settings and normalize values."""
    document = normalized_settings_document(load_settings_file())
    normalized_payload = payload if isinstance(payload, dict) else {}
    global_settings = build_global_settings({**document["global"], **normalized_payload})
    if "tally_company" in normalized_payload and "selected_company" not in normalized_payload:
        global_settings["selected_company"] = str(normalized_payload.get("tally_company") or "").strip()
    selected_company = global_settings.get("selected_company", "")
    mapping = build_company_mapping({key: normalized_payload[key] for key in COMPANY_MAPPING_KEYS if key in normalized_payload})
    return TallySettings(
        tally_url=global_settings["tally_url"],
        tally_company=selected_company,
        invoiceai_license_file=global_settings["invoiceai_license_file"],
        tally_timeout_seconds=global_settings["tally_timeout_seconds"],
        **mapping,
    )


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
