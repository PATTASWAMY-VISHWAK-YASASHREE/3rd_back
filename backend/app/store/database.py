"""
Database setup using SQLAlchemy async + SQLite.
Zero cost, no cloud account needed, single-file database.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Text, DateTime, JSON
from datetime import datetime
from typing import Optional


class Base(DeclarativeBase):
    pass


class TestSuiteDB(Base):
    __tablename__ = "test_suites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    suite_id: Mapped[str] = mapped_column(unique=True, index=True)
    user_story: Mapped[str] = mapped_column(Text)
    acceptance_criteria: Mapped[dict] = mapped_column(JSON, default=list)
    component: Mapped[str] = mapped_column(default="General")
    priority: Mapped[str] = mapped_column(default="P1")
    format: Mapped[str] = mapped_column(default="gherkin")
    total_cases: Mapped[int] = mapped_column(default=0)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    test_cases_json: Mapped[dict] = mapped_column(JSON)
    project_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    task_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )


# Engine and session factory — lazily initialized
_engine = None
_async_session = None


def _get_engine():
    global _engine
    if _engine is None:
        from app.config import get_settings
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.log_level == "DEBUG",
        )
    return _engine


def _get_session_factory():
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            _get_engine(), expire_on_commit=False
        )
    return _async_session


async def init_db():
    """Create all tables. Call once on startup."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    session_factory = _get_session_factory()
    async with session_factory() as session:
        yield session
