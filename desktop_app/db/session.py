from __future__ import annotations

"""Engine and session helpers for the desktop database."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import DATABASE_URL
from .migrations import apply_startup_migrations

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create desktop database tables and apply safe startup migrations."""
    apply_startup_migrations(engine)


def session_scope() -> Session:
    """Return a new SQLAlchemy session for one workflow operation."""
    return SessionLocal()
