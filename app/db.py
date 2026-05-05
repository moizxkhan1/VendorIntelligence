from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_url(db_path: str) -> str:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{p.as_posix()}"


engine = create_async_engine(_build_url(settings.db_path), future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create tables for any models registered on Base. Idempotent."""
    from app import models  # noqa: F401  ensure models import to register on Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session per request."""
    async with SessionLocal() as session:
        yield session
