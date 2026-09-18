from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRecord,
    EpisodeStatus,
    EpisodeValidationRunRecord,
)
from sceneops_core.runs.schemas import RunStatus, RunType

EpisodeRunRecord: TypeAlias = EpisodeValidationRunRecord | EpisodeProfileRunRecord


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
        mission_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]: ...


@runtime_checkable
class EpisodeRunRepository(Protocol):
    async def create(self, run: EpisodeRunRecord) -> EpisodeRunRecord: ...

    async def get(self, run_id: str) -> EpisodeRunRecord | None: ...

    async def update(self, run: EpisodeRunRecord) -> EpisodeRunRecord: ...

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        episode_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRunRecord]: ...
