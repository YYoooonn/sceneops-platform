from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRecord,
    EpisodeRevisionRecord,
    EpisodeValidationRunRecord,
)
from sceneops_core.runs.schemas import RunStatus, RunType

from .revisions import RevisionRegistration

EpisodeRunRecord: TypeAlias = EpisodeValidationRunRecord | EpisodeProfileRunRecord


@runtime_checkable
class EpisodeRepository(Protocol):
    """Episode units and their revisions (ADR-015 §3, §6.4). Read access
    for everyone; ``register_revision`` is for the Episode registrar only,
    and it only adds rows (``sceneops_db.repositories.revisions``)."""

    async def get(self, episode_id: str) -> EpisodeRecord | None: ...

    async def list(
        self,
        *,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRecord]: ...

    async def get_revision(
        self, *, episode_id: str, manifest_checksum: str
    ) -> EpisodeRevisionRecord | None: ...

    async def list_revisions(self, episode_id: str) -> list[EpisodeRevisionRecord]: ...

    async def register_revision(
        self, revision: EpisodeRevisionRecord
    ) -> RevisionRegistration[EpisodeRevisionRecord]: ...

    # --- the revisions a DatasetVersion pins -----------------------------------

    async def get_member_revision(
        self, *, dataset_id: str, dataset_version: str, episode_id: str
    ) -> EpisodeRevisionRecord | None: ...

    async def list_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EpisodeRevisionRecord]: ...

    async def count_members(self, *, dataset_id: str, dataset_version: str) -> int: ...


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
