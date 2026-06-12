from __future__ import annotations

"""Engine and session helpers for the desktop database."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATABASE_URL
from .models import Base

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create desktop database tables when they do not already exist."""
    Base.metadata.create_all(engine)
    ensure_source_metadata_columns()


def session_scope() -> Session:
    """Return a new SQLAlchemy session for one workflow operation."""
    return SessionLocal()


def ensure_source_metadata_columns() -> None:
    """Add nullable source metadata columns for existing SQLite databases."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        inspector = inspect(connection)
        if "invoice_extractions" not in inspector.get_table_names():
            return
        columns = {column["name"] for column in inspector.get_columns("invoice_extractions")}
        if "document_kind" not in columns:
            connection.execute(text("ALTER TABLE invoice_extractions ADD COLUMN document_kind VARCHAR(30)"))
        if "mime_type" not in columns:
            connection.execute(text("ALTER TABLE invoice_extractions ADD COLUMN mime_type VARCHAR(100)"))
