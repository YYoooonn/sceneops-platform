from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRecord,
    EpisodeValidationRunRecord,
)
from sceneops_core.runs.schemas import RunStatus, RunType

EpisodeRunRecord: TypeAlias = EpisodeValidationRunRecord | EpisodeProfileRunRecord


@runtime_checkable
class EpisodeRepository(Protocol):
    """Read access for everyone; ``insert`` / ``replace_revision`` /
    ``delete`` are for the Episode registrar only (ADR-007 §17.5)."""

    async def get(self, episode_id: str) -> EpisodeRecord | None: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]: ...

    async def list_recording_scope(
        self, *, dataset_id: str, dataset_version: str, robot_run_id: str
    ) -> list[EpisodeRecord]: ...

    async def count(
        self, *, dataset_id: str | None = None, dataset_version: str | None = None
    ) -> int: ...

    async def insert(self, episode: EpisodeRecord) -> EpisodeRecord: ...

    async def replace_revision(self, episode: EpisodeRecord) -> EpisodeRecord: ...

    async def delete(self, episode_ids: list[str]) -> int: ...


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
        manifest_artifact_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRunRecord]: ...
