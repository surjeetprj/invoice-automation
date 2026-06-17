from __future__ import annotations

"""SQLite startup migrations for local desktop databases."""

import json
import logging
from collections.abc import Mapping
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..domain.schemas import InvoiceData, ValidationIssue, ValidationResult
from .models import Base, InvoiceValidationIssue
from .repository import build_extraction

logger = logging.getLogger(__name__)

LEGACY_EXTRACTED_DATA_WARNING = "Legacy extracted_data could not be parsed"

INVOICE_COLUMN_DDL = {
    "file_hash": "ALTER TABLE invoices ADD COLUMN file_hash VARCHAR(64)",
    "invoice_number_extracted": "ALTER TABLE invoices ADD COLUMN invoice_number_extracted VARCHAR(100)",
    "invoice_date_extracted": "ALTER TABLE invoices ADD COLUMN invoice_date_extracted VARCHAR(30)",
    "total_amount_extracted": "ALTER TABLE invoices ADD COLUMN total_amount_extracted FLOAT",
    "vendor_gstin": "ALTER TABLE invoices ADD COLUMN vendor_gstin VARCHAR(15)",
    "supply_type": "ALTER TABLE invoices ADD COLUMN supply_type VARCHAR(20)",
    "confidence_score": "ALTER TABLE invoices ADD COLUMN confidence_score FLOAT",
    "processing_time_ms": "ALTER TABLE invoices ADD COLUMN processing_time_ms INTEGER",
    "reviewed_by": "ALTER TABLE invoices ADD COLUMN reviewed_by VARCHAR(100)",
    "reviewed_at": "ALTER TABLE invoices ADD COLUMN reviewed_at DATETIME",
    "rejection_reason": "ALTER TABLE invoices ADD COLUMN rejection_reason TEXT",
    "created_at": "ALTER TABLE invoices ADD COLUMN created_at DATETIME",
    "updated_at": "ALTER TABLE invoices ADD COLUMN updated_at DATETIME",
}

EXTRACTION_COLUMN_DDL = {
    "document_kind": "ALTER TABLE invoice_extractions ADD COLUMN document_kind VARCHAR(30)",
    "mime_type": "ALTER TABLE invoice_extractions ADD COLUMN mime_type VARCHAR(100)",
}

LINE_ITEM_COLUMN_DDL = {
    "item_name": "ALTER TABLE invoice_line_items ADD COLUMN item_name VARCHAR(255)",
}

INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_invoices_status ON invoices(status)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_invoice_number_extracted ON invoices(invoice_number_extracted)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_vendor_gstin ON invoices(vendor_gstin)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_created_at ON invoices(created_at)",
    "CREATE INDEX IF NOT EXISTS ix_invoices_file_hash ON invoices(file_hash)",
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_invoice_id ON audit_logs(invoice_id)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_line_items_extraction_id ON invoice_line_items(extraction_id)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_line_taxes_line_item_id ON invoice_line_taxes(line_item_id)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_tax_breakups_extraction_id ON invoice_tax_breakups(extraction_id)",
    "CREATE INDEX IF NOT EXISTS ix_invoice_validation_issues_invoice_id ON invoice_validation_issues(invoice_id)",
)


def apply_startup_migrations(bind: Engine) -> None:
    """Create current schema and backfill legacy SQLite invoice JSON rows."""
    if bind.dialect.name != "sqlite":
        Base.metadata.create_all(bind)
        return

    ensure_invoice_columns(bind)
    Base.metadata.create_all(bind)
    ensure_source_metadata_columns(bind)
    ensure_line_item_columns(bind)
    ensure_indexes(bind)
    backfill_legacy_invoice_extractions(bind)


def ensure_invoice_columns(bind: Engine) -> None:
    """Add nullable current ORM columns to existing legacy invoice tables."""
    with bind.begin() as connection:
        inspector = inspect(connection)
        if "invoices" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("invoices")}
        for column, ddl in INVOICE_COLUMN_DDL.items():
            if column not in columns:
                logger.info("Adding missing invoices.%s column", column)
                connection.execute(text(ddl))


def ensure_source_metadata_columns(bind: Engine) -> None:
    """Add nullable source metadata columns for existing normalized databases."""
    with bind.begin() as connection:
        inspector = inspect(connection)
        if "invoice_extractions" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("invoice_extractions")}
        for column, ddl in EXTRACTION_COLUMN_DDL.items():
            if column not in columns:
                logger.info("Adding missing invoice_extractions.%s column", column)
                connection.execute(text(ddl))


def ensure_line_item_columns(bind: Engine) -> None:
    """Add nullable line-item columns for existing normalized databases."""
    with bind.begin() as connection:
        inspector = inspect(connection)
        if "invoice_line_items" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("invoice_line_items")}
        for column, ddl in LINE_ITEM_COLUMN_DDL.items():
            if column not in columns:
                logger.info("Adding missing invoice_line_items.%s column", column)
                connection.execute(text(ddl))


def ensure_indexes(bind: Engine) -> None:
    """Create expected indexes that create_all will skip on pre-existing tables."""
    with bind.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        for ddl in INDEX_DDL:
            table = ddl.rsplit(" ON ", maxsplit=1)[1].split("(", maxsplit=1)[0]
            if table in tables:
                connection.execute(text(ddl))


def backfill_legacy_invoice_extractions(bind: Engine) -> None:
    """Backfill normalized rows from legacy invoices.extracted_data JSON."""
    with bind.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if "invoices" not in tables or "invoice_extractions" not in tables:
            return

        invoice_columns = {column["name"] for column in inspector.get_columns("invoices")}
        if "extracted_data" not in invoice_columns:
            return

        selected_columns = [
            "id",
            "filename",
            "file_path",
            "extracted_data",
            "raw_markdown" if "raw_markdown" in invoice_columns else "NULL AS raw_markdown",
            "validation_result" if "validation_result" in invoice_columns else "NULL AS validation_result",
        ]
        rows = connection.execute(text(f"SELECT {', '.join(selected_columns)} FROM invoices")).mappings().all()
        if not rows:
            return

        migrated_invoice_ids = set(
            connection.execute(text("SELECT invoice_id FROM invoice_extractions")).scalars()
        )
        session = Session(bind=connection, expire_on_commit=False, future=True)
        migrated_count = 0
        try:
            for row in rows:
                invoice_id = row["id"]
                if invoice_id in migrated_invoice_ids:
                    continue

                migration = build_legacy_migration_rows(row)
                if migration is None:
                    continue

                extraction, validation_issues = migration
                extraction.invoice_id = invoice_id
                session.add(extraction)
                for issue in validation_issues:
                    session.add(
                        InvoiceValidationIssue(
                            invoice_id=invoice_id,
                            severity=issue.severity,
                            message=issue.message,
                            field=issue.field,
                        )
                    )
                migrated_count += 1
            session.flush()
        finally:
            session.close()

        if migrated_count:
            logger.info("Backfilled normalized extraction rows for %d legacy invoice(s)", migrated_count)


def build_legacy_migration_rows(
    row: Mapping[str, Any],
) -> tuple[Any, list[ValidationIssue]] | None:
    """Build normalized extraction and issue rows for one legacy invoice row."""
    raw_markdown = useful_text(row.get("raw_markdown"))
    extracted_payload, extracted_malformed = parse_json_object(row.get("extracted_data"))
    data = invoice_data_from_legacy_payload(extracted_payload)
    data_malformed = extracted_malformed or (extracted_payload is not None and data is None)
    validation_issues: list[ValidationIssue] = []

    if data is None:
        if not raw_markdown:
            return None
        data = InvoiceData()
        if data_malformed:
            validation_issues.append(
                ValidationIssue(
                    severity="warning",
                    message=LEGACY_EXTRACTED_DATA_WARNING,
                    field="Legacy Migration",
                )
            )
    else:
        validation_issues = validation_issues_from_legacy_payload(row.get("validation_result"))

    document_kind, mime_type = infer_legacy_source_metadata(row, raw_markdown, extracted_payload is not None)
    extraction = build_extraction(data, raw_markdown, document_kind=document_kind, mime_type=mime_type)
    return extraction, validation_issues


def invoice_data_from_legacy_payload(payload: dict[str, Any] | None) -> InvoiceData | None:
    """Parse legacy extracted_data JSON into InvoiceData without raising."""
    if payload is None:
        return None
    try:
        return InvoiceData(**payload)
    except Exception:
        logger.exception("Could not parse legacy extracted_data during startup migration")
        return None


def validation_issues_from_legacy_payload(value: Any) -> list[ValidationIssue]:
    """Parse legacy validation_result JSON into display issue rows."""
    payload, malformed = parse_json_object(value)
    if payload is None:
        if malformed:
            logger.warning("Could not parse legacy validation_result during startup migration")
        return []
    try:
        validation = ValidationResult(**payload)
    except Exception:
        logger.exception("Could not validate legacy validation_result during startup migration")
        return []
    return [
        ValidationIssue(severity=issue.severity, message=issue.message, field=issue.field)
        for issue in validation.issues
    ]


def parse_json_object(value: Any) -> tuple[dict[str, Any] | None, bool]:
    """Return a JSON object payload and whether a non-empty value was malformed."""
    if value is None:
        return None, False
    if not isinstance(value, str):
        return None, True
    if not value.strip():
        return None, False
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(payload, dict):
        return None, True
    return payload, False


def useful_text(value: Any) -> str | None:
    """Return stripped legacy text only when it contains useful content."""
    if not isinstance(value, str):
        return None
    return value if value.strip() else None


def infer_legacy_source_metadata(
    row: Mapping[str, Any],
    raw_markdown: str | None,
    has_extracted_payload: bool,
) -> tuple[str | None, str | None]:
    """Infer source metadata for migrated rows from legacy file names."""
    filename = str(row.get("filename") or "").lower()
    file_path = str(row.get("file_path") or "").lower()
    is_pdf = filename.endswith(".pdf") or file_path.endswith(".pdf")
    if not is_pdf:
        return None, None
    document_kind = "DIGITAL_PDF" if raw_markdown or has_extracted_payload else None
    return document_kind, "application/pdf"
