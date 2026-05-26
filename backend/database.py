"""
Async SQLAlchemy setup for SQLite.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import DATABASE_URL

# ──────────────────────────────────────────────
# Engine & Session
# ──────────────────────────────────────────────
engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ──────────────────────────────────────────────
# Base class for ORM models
# ──────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# Dependency for FastAPI routes
# ──────────────────────────────────────────────
async def get_db():
    """Yield an async DB session, auto-close on exit."""
    async with AsyncSessionLocal() as session:
        yield session


# ──────────────────────────────────────────────
# Table creation helper (called on startup)
# ──────────────────────────────────────────────
async def init_db():
    """Create all tables defined on Base.metadata."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
