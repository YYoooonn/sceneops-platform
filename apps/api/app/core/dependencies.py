from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ApiSettings, get_settings
from sceneops_db.session import get_async_sessionmaker
from sceneops_storage import ArtifactStore, create_artifact_store


ApiSettingsDep = Annotated[ApiSettings, Depends(get_settings)]


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """The request's single transaction.

    Committed when the path operation returns and *before* the response is sent
    (``scope="function"`` below), so a success response always describes committed
    state and a failed commit is reported as an error rather than after a 2xx.
    Any exception, HTTPException included, rolls the whole request back. Services
    and repositories flush but never commit.

    Operations that must commit and then talk to an external system (Celery) do not
    use this session: they open their own short sessions and commit explicitly
    before dispatching.
    """
    async with get_async_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session, scope="function")]


@lru_cache
def _build_artifact_store() -> ArtifactStore:
    return create_artifact_store(get_settings().artifact)


def get_artifact_store() -> ArtifactStore:
    return _build_artifact_store()


ArtifactStoreDep = Annotated[ArtifactStore, Depends(get_artifact_store)]
