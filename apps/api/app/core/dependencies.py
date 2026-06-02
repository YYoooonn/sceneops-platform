from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ApiSettings, get_settings
from sceneops_db.session import async_session_scope
from sceneops_storage import ArtifactStore, create_artifact_store


def get_api_settings() -> ApiSettings:
    return get_settings()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_scope() as session:
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
ApiSettingsDep = Annotated[ApiSettings, Depends(get_api_settings)]


def get_artifact_store(
    settings: ApiSettingsDep,
) -> ArtifactStore:
    return create_artifact_store(settings.artifact)


ArtifactStoreDep = Annotated[ArtifactStore, Depends(get_artifact_store)]
