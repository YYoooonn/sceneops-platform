from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.episodes.schemas import EpisodeRecord, EpisodeStatus


@runtime_checkable
class EpisodeRepository(Protocol):
    async def create(self, episode: EpisodeRecord) -> EpisodeRecord: ...

    async def upsert(self, episode: EpisodeRecord) -> EpisodeRecord: ...

    async def get(self, episode_id: str) -> EpisodeRecord | None: ...

    async def update(self, episode: EpisodeRecord) -> EpisodeRecord: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        status: EpisodeStatus | None = None,
        robot_id: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]: ...
