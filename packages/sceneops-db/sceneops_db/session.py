from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from threading import Lock

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sceneops_db.config import get_db_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_lock = Lock()


def get_async_engine() -> AsyncEngine:
    """Return process-local async SQLAlchemy engine."""

    with _lock:
        return _get_async_engine_locked()


def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return process-local async sessionmaker."""

    global _sessionmaker

    with _lock:
        if _sessionmaker is None:
            _sessionmaker = async_sessionmaker(
                bind=_get_async_engine_locked(),
                class_=AsyncSession,
                expire_on_commit=False,
            )

        return _sessionmaker


def _get_async_engine_locked() -> AsyncEngine:
    """Return engine while caller already holds _lock."""

    global _engine

    if _engine is None:
        settings = get_db_settings()

        _engine = create_async_engine(
            settings.sceneops_database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=10,
            pool_recycle=1800,
        )

    return _engine


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = get_async_sessionmaker()

    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def async_session_scope() -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = get_async_sessionmaker()

    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def dispose_async_engine() -> None:
    """Dispose the process-local async engine while event loop is alive."""

    global _engine
    global _sessionmaker

    with _lock:
        engine = _engine
        _sessionmaker = None
        _engine = None

    if engine is not None:
        await engine.dispose()


def reset_async_engine_cache() -> None:
    """Reset process-local DB singletons after Celery fork."""

    global _engine
    global _sessionmaker

    with _lock:
        _sessionmaker = None
        _engine = None
