"""Async engine / session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_read_engine: AsyncEngine | None = None
_read_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


def read_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Sessions for report queries: the streaming replica when DATABASE_READ_URL is set (design DS-23).

    Only used for read-only endpoints that tolerate replication lag (reports, report exports); anything that
    writes, claims or must read its own writes uses the primary.
    """
    global _read_engine, _read_sessionmaker
    url = get_settings().database_read_url
    if not url:
        return sessionmaker()
    if _read_sessionmaker is None:
        _read_engine = create_async_engine(url, pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=1800)
        _read_sessionmaker = async_sessionmaker(_read_engine, expire_on_commit=False)
    return _read_sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker, _read_engine, _read_sessionmaker
    for engine in (_engine, _read_engine):
        if engine is not None:
            await engine.dispose()
    _engine = _read_engine = None
    _sessionmaker = _read_sessionmaker = None


async def get_db() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


async def get_read_db() -> AsyncIterator[AsyncSession]:
    async with read_sessionmaker()() as session:
        yield session
