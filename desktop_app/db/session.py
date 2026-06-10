from __future__ import annotations

"""Engine, session, and lightweight migrations for the desktop database."""

import logging
import shutil
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATABASE_URL, LEGACY_RUNTIME_DIR, RUNTIME_DIR
from .models import Base

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create tables and apply lightweight migrations for existing SQLite DBs."""
    migrate_legacy_runtime()
    Base.metadata.create_all(engine)
    apply_lightweight_migrations()


def session_scope() -> Session:
    """Return a new SQLAlchemy session for one workflow operation."""
    return SessionLocal()


def migrate_legacy_runtime() -> None:
    """Copy old source-local runtime data to app-data on first run."""
    if not LEGACY_RUNTIME_DIR.exists() or LEGACY_RUNTIME_DIR.resolve() == RUNTIME_DIR.resolve():
        return
    if (RUNTIME_DIR / "invoices.db").exists():
        return
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        legacy_db = LEGACY_RUNTIME_DIR / "invoices.db"
        if legacy_db.exists():
            shutil.copy2(legacy_db, RUNTIME_DIR / "invoices.db")
        for name in ("uploads", "exports"):
            source = LEGACY_RUNTIME_DIR / name
            target = RUNTIME_DIR / name
            if source.exists() and not target.exists():
                shutil.copytree(source, target)
        logger.info("Migrated legacy desktop runtime data to %s", RUNTIME_DIR)
    except OSError:
        logger.exception("Could not migrate legacy runtime data from %s", LEGACY_RUNTIME_DIR)


def apply_lightweight_migrations() -> None:
    """Add missing columns/indexes for users upgrading from early desktop builds."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(invoices)"))}
        for column, ddl in {
            "file_hash": "ALTER TABLE invoices ADD COLUMN file_hash VARCHAR(64)",
            "invoice_date_extracted": "ALTER TABLE invoices ADD COLUMN invoice_date_extracted VARCHAR(30)",
            "total_amount_extracted": "ALTER TABLE invoices ADD COLUMN total_amount_extracted FLOAT",
        }.items():
            if column not in columns:
                conn.execute(text(ddl))
        for ddl in (
            "CREATE INDEX IF NOT EXISTS ix_invoices_status ON invoices(status)",
            "CREATE INDEX IF NOT EXISTS ix_invoices_invoice_number_extracted ON invoices(invoice_number_extracted)",
            "CREATE INDEX IF NOT EXISTS ix_invoices_vendor_gstin ON invoices(vendor_gstin)",
            "CREATE INDEX IF NOT EXISTS ix_invoices_created_at ON invoices(created_at)",
            "CREATE INDEX IF NOT EXISTS ix_invoices_file_hash ON invoices(file_hash)",
            "CREATE INDEX IF NOT EXISTS ix_audit_logs_invoice_id ON audit_logs(invoice_id)",
        ):
            conn.execute(text(ddl))
