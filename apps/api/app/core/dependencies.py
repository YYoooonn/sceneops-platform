from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ApiSettings, get_settings
from sceneops_db.session import get_db_session as _sceneops_db_session
from sceneops_storage import ArtifactStore, create_artifact_store


ApiSettingsDep = Annotated[ApiSettings, Depends(get_settings)]


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async for session in _sceneops_db_session():
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


@lru_cache
def _build_artifact_store() -> ArtifactStore:
    return create_artifact_store(get_settings().artifact)


def get_artifact_store() -> ArtifactStore:
    return _build_artifact_store()


ArtifactStoreDep = Annotated[ArtifactStore, Depends(get_artifact_store)]
