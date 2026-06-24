from __future__ import annotations

"""SQL-backed Tally master mappings and review suggestions."""

from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from difflib import SequenceMatcher
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import config
from ...db.models import TallyMasterMapping
from ...domain.schemas import InvoiceData

DEFAULT_BIZ_ID = 1
DEFAULT_SOURCE = "DEFAULT"

VENDOR_GROUP = "VENDOR_GROUP"
STOCK_GROUP = "STOCK_GROUP"
VENDOR_LEDGER = "VENDOR_LEDGER"
PURCHASE_LEDGER = "PURCHASE_LEDGER"
STOCK_ITEM = "STOCK_ITEM"
UNIT = "UNIT"
INPUT_CGST_LEDGER = "INPUT_CGST_LEDGER"
INPUT_SGST_LEDGER = "INPUT_SGST_LEDGER"
INPUT_IGST_LEDGER = "INPUT_IGST_LEDGER"
INPUT_CESS_LEDGER = "INPUT_CESS_LEDGER"
ROUND_OFF_LEDGER = "ROUND_OFF_LEDGER"
FREIGHT_LEDGER = "FREIGHT_LEDGER"
DISCOUNT_LEDGER = "DISCOUNT_LEDGER"
PACKING_CHARGES_LEDGER = "PACKING_CHARGES_LEDGER"

SETTINGS_KEY_TO_MAPPING_TYPE = {
    "tally_vendor_parent_ledger": VENDOR_GROUP,
    "default_stock_group": STOCK_GROUP,
    "purchase_ledger_name": PURCHASE_LEDGER,
    "input_cgst_ledger_name": INPUT_CGST_LEDGER,
    "input_sgst_ledger_name": INPUT_SGST_LEDGER,
    "input_igst_ledger_name": INPUT_IGST_LEDGER,
    "input_cess_ledger_name": INPUT_CESS_LEDGER,
}
MAPPING_TYPE_TO_SETTINGS_KEY = {value: key for key, value in SETTINGS_KEY_TO_MAPPING_TYPE.items()}

CURRENT_CONTEXT: ContextVar[dict[tuple[str, str], str]] = ContextVar("tally_master_mapping_context", default={})


def normalize_text(value: Any) -> str:
    """Normalize one persisted mapping value without changing its meaning."""
    return str(value or "").strip()


def mapping_type(value: Any) -> str:
    """Normalize mapping type names to the stored uppercase representation."""
    return normalize_text(value).upper()


def mapping_key(type_value: Any, source_value: Any) -> tuple[str, str]:
    """Return the in-memory context key for one mapping."""
    return (mapping_type(type_value), normalize_text(source_value))


def default_company_mapping() -> dict[str, str]:
    """Return config-backed defaults for Settings mapping fields."""
    return {
        "tally_vendor_parent_ledger": config.TALLY_VENDOR_PARENT_LEDGER,
        "default_stock_group": config.DEFAULT_STOCK_GROUP,
        "purchase_ledger_name": config.PURCHASE_LEDGER_NAME,
        "input_cgst_ledger_name": config.INPUT_CGST_LEDGER_NAME,
        "input_sgst_ledger_name": config.INPUT_SGST_LEDGER_NAME,
        "input_igst_ledger_name": config.INPUT_IGST_LEDGER_NAME,
        "input_cess_ledger_name": config.INPUT_CESS_LEDGER_NAME,
    }


def get_mapping(
    db: Session,
    company_name: str,
    type_value: str,
    source_value: str,
    *,
    biz_id: int = DEFAULT_BIZ_ID,
) -> str | None:
    """Return an active SQL mapping value if one exists."""
    row = db.scalar(
        select(TallyMasterMapping).where(
            TallyMasterMapping.biz_id == biz_id,
            TallyMasterMapping.company_name == normalize_text(company_name),
            TallyMasterMapping.mapping_type == mapping_type(type_value),
            TallyMasterMapping.source_value == normalize_text(source_value),
            TallyMasterMapping.is_active == "Y",
        )
    )
    return normalize_text(row.tally_value) if row and normalize_text(row.tally_value) else None


def save_mapping(
    db: Session,
    company_name: str,
    type_value: str,
    source_value: str,
    tally_value: str | None,
    *,
    is_active: str = "Y",
    biz_id: int = DEFAULT_BIZ_ID,
    overwrite: bool = True,
) -> TallyMasterMapping | None:
    """Create or update one active SQL mapping row."""
    company_name = normalize_text(company_name)
    stored_type = mapping_type(type_value)
    source_value = normalize_text(source_value)
    tally_value = normalize_text(tally_value)
    is_active = "Y" if normalize_text(is_active).upper() != "N" else "N"
    if not company_name or not stored_type or not source_value:
        return None
    row = db.scalar(
        select(TallyMasterMapping).where(
            TallyMasterMapping.biz_id == biz_id,
            TallyMasterMapping.company_name == company_name,
            TallyMasterMapping.mapping_type == stored_type,
            TallyMasterMapping.source_value == source_value,
            TallyMasterMapping.is_active == "Y",
        )
    )
    if row is None:
        row = TallyMasterMapping(
            biz_id=biz_id,
            company_name=company_name,
            mapping_type=stored_type,
            source_value=source_value,
            tally_value=tally_value,
            is_active=is_active,
        )
        db.add(row)
    elif overwrite:
        row.tally_value = tally_value
        row.is_active = is_active
    return row


def save_mappings(db: Session, company_name: str, rows: Iterable[dict[str, Any]], *, biz_id: int = DEFAULT_BIZ_ID) -> int:
    """Persist editable review-page mapping rows."""
    saved = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if save_mapping(
            db,
            company_name,
            row.get("mapping_type", ""),
            row.get("source_value", ""),
            row.get("tally_value"),
            is_active=row.get("is_active", "Y"),
            biz_id=biz_id,
        ) is not None:
            saved += 1
    db.flush()
    return saved



def migrate_legacy_settings_mappings(db: Session) -> int:
    """Copy explicitly saved legacy settings.json company mappings into SQL once."""
    from ..settings import load_settings_file, normalized_settings_document

    content = load_settings_file()
    document = normalized_settings_document(content)
    raw_tally = content.get("tally", content) if isinstance(content, dict) else {}
    raw_tally = raw_tally if isinstance(raw_tally, dict) else {}
    raw_companies = raw_tally.get("companies") if isinstance(raw_tally.get("companies"), dict) else {}
    if not raw_companies:
        selected = document.get("global", {}).get("selected_company")
        raw_mapping = {key: raw_tally[key] for key in SETTINGS_KEY_TO_MAPPING_TYPE if key in raw_tally}
        raw_companies = {selected: raw_mapping} if selected and raw_mapping else {}

    migrated = 0
    for company_name, mapping in raw_companies.items():
        if not isinstance(mapping, dict):
            continue
        for key, type_value in SETTINGS_KEY_TO_MAPPING_TYPE.items():
            if key not in mapping:
                continue
            value = normalize_text(mapping.get(key))
            if not value:
                continue
            existing = get_mapping(db, company_name, type_value, DEFAULT_SOURCE)
            if existing:
                continue
            if save_mapping(db, company_name, type_value, DEFAULT_SOURCE, value, overwrite=False) is not None:
                migrated += 1
    if migrated:
        db.flush()
    return migrated

def settings_mapping_from_db(db: Session, company_name: str, *, biz_id: int = DEFAULT_BIZ_ID) -> dict[str, str]:
    """Return effective Settings mapping values from SQL plus config defaults."""
    values = default_company_mapping()
    for key, type_value in SETTINGS_KEY_TO_MAPPING_TYPE.items():
        mapped = get_mapping(db, company_name, type_value, DEFAULT_SOURCE, biz_id=biz_id)
        if mapped:
            values[key] = mapped
    return values


def save_settings_mapping(db: Session, company_name: str, payload: dict[str, Any], *, overwrite: bool = True) -> int:
    """Save Settings page mapping fields as DEFAULT SQL mappings."""
    saved = 0
    for key, type_value in SETTINGS_KEY_TO_MAPPING_TYPE.items():
        if key not in payload:
            continue
        if save_mapping(db, company_name, type_value, DEFAULT_SOURCE, payload.get(key), overwrite=overwrite) is not None:
            saved += 1
    db.flush()
    return saved


def all_company_mappings(db: Session, *, biz_id: int = DEFAULT_BIZ_ID) -> dict[str, dict[str, str]]:
    """Return SQL DEFAULT mappings grouped by company for the Settings dialog."""
    rows = db.scalars(
        select(TallyMasterMapping).where(
            TallyMasterMapping.biz_id == biz_id,
            TallyMasterMapping.source_value == DEFAULT_SOURCE,
            TallyMasterMapping.is_active == "Y",
        )
    ).all()
    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        company = normalize_text(row.company_name)
        key = MAPPING_TYPE_TO_SETTINGS_KEY.get(mapping_type(row.mapping_type))
        value = normalize_text(row.tally_value)
        if company and key and value:
            grouped.setdefault(company, default_company_mapping())[key] = value
    return grouped


def dynamic_mapping_rows(
    db: Session,
    data: InvoiceData | None,
    company_name: str,
    *,
    candidates: dict[str, Sequence[str]] | None = None,
    biz_id: int = DEFAULT_BIZ_ID,
) -> list[dict[str, Any]]:
    """Build invoice-review mapping rows that are not covered by Settings."""
    if data is None or not normalize_text(company_name):
        return []
    rows: list[dict[str, Any]] = []
    candidates = candidates or {}

    def append_row(type_value: str, source_value: str, fallback: str) -> None:
        source_value = normalize_text(source_value)
        if not source_value:
            return
        mapped = get_mapping(db, company_name, type_value, source_value, biz_id=biz_id)
        ranked = ranked_candidates(source_value, candidates.get(type_value, ()))
        suggested = ranked[0]["value"] if ranked else fallback
        rows.append({
            "mapping_type": mapping_type(type_value),
            "source_value": source_value,
            "company_name": normalize_text(company_name),
            "tally_value": mapped or suggested,
            "is_active": "Y",
            "candidates": [item["value"] for item in ranked],
            "match_score": ranked[0]["score"] if ranked else None,
            "auto_matched": bool(not mapped and ranked),
        })

    append_row(VENDOR_LEDGER, data.vendor_name or "Unknown Supplier", data.vendor_name or "Unknown Supplier")
    seen_items: set[str] = set()
    seen_units: set[str] = set()
    for item in data.line_items:
        item_source = normalize_text(item.item_name or item.description)
        if item_source and item_source not in seen_items:
            append_row(STOCK_ITEM, item_source, item_source)
            seen_items.add(item_source)
        unit_source = normalize_text(item.unit)
        if unit_source and unit_source not in seen_units:
            append_row(UNIT, unit_source, unit_source)
            seen_units.add(unit_source)
    if data.round_off:
        append_row(ROUND_OFF_LEDGER, DEFAULT_SOURCE, "Round Off")
    return rows



def context_rows_for_settings(db: Session, company_name: str) -> list[dict[str, Any]]:
    """Return effective company-level Settings mappings used by Tally XML builders."""
    rows: list[dict[str, Any]] = []
    for key, type_value in SETTINGS_KEY_TO_MAPPING_TYPE.items():
        default_value = default_company_mapping()[key]
        rows.append({
            "mapping_type": type_value,
            "source_value": DEFAULT_SOURCE,
            "tally_value": get_mapping(db, company_name, type_value, DEFAULT_SOURCE) or default_value,
            "is_active": "Y",
        })
    return rows


def context_rows_for_invoice(db: Session, data: InvoiceData | None, company_name: str) -> list[dict[str, Any]]:
    """Return effective mappings used while generating Tally XML."""
    rows = context_rows_for_settings(db, company_name)
    if data is not None:
        rows.extend(dynamic_mapping_rows(db, data, company_name))
    return rows


@contextmanager
def tally_mapping_context(rows: Iterable[dict[str, Any]]):
    """Expose SQL-resolved mapping rows to Tally XML builders during one operation."""
    values: dict[tuple[str, str], str] = {}
    for row in rows or []:
        mapped = normalize_text(row.get("tally_value"))
        if mapped and normalize_text(row.get("is_active", "Y")).upper() == "Y":
            values[mapping_key(row.get("mapping_type", ""), row.get("source_value", ""))] = mapped
    token = CURRENT_CONTEXT.set(values)
    try:
        yield
    finally:
        CURRENT_CONTEXT.reset(token)


def mapped_value(type_value: str, source_value: str, fallback: str | None = None) -> str:
    """Return context mapping value or fallback."""
    return CURRENT_CONTEXT.get({}).get(mapping_key(type_value, source_value), normalize_text(fallback))


def mapped_default(type_value: str, fallback: str | None = None) -> str:
    """Return DEFAULT context mapping value or fallback."""
    return mapped_value(type_value, DEFAULT_SOURCE, fallback)


def comparable_text(value: str) -> str:
    """Normalize a value for similarity matching only."""
    text = normalize_text(value).lower()
    text = re.sub(r"\b(private|pvt|limited|ltd|llp|inc|company|co|the)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def similarity_score(source_value: str, candidate: str) -> float:
    """Return deterministic string similarity between two master names."""
    source = comparable_text(source_value)
    target = comparable_text(candidate)
    if not source or not target:
        return 0.0
    if source == target:
        return 1.0
    if source in target or target in source:
        return max(0.9, SequenceMatcher(None, source, target).ratio())
    return SequenceMatcher(None, source, target).ratio()


def ranked_candidates(source_value: str, candidates: Sequence[str], *, limit: int = 25) -> list[dict[str, Any]]:
    """Return unique candidates sorted by descending similarity score."""
    unique: list[str] = []
    for candidate in candidates:
        cleaned = normalize_text(candidate)
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    ranked = sorted(
        ({"value": candidate, "score": round(similarity_score(source_value, candidate), 4)} for candidate in unique),
        key=lambda item: (-item["score"], item["value"].casefold()),
    )
    return ranked[:limit]
