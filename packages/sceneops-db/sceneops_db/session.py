from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sceneops_db.config import get_db_settings


@lru_cache
def get_async_engine() -> AsyncEngine:
    settings = get_db_settings()

    return create_async_engine(
        settings.sceneops_database_url,
        echo=False,
        pool_pre_ping=True,
    )


@lru_cache
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = get_async_sessionmaker()

    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def async_session_scope() -> AsyncGenerator[AsyncSession, None]:
    sessionmaker = get_async_sessionmaker()

    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
