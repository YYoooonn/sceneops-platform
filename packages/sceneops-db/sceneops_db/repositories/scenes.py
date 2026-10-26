from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas import SceneRecord, SceneRevisionRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

from .revisions import RevisionRegistration

SceneRunRecord: TypeAlias = SceneValidationRunRecord | SceneProfileRunRecord


@runtime_checkable
class SceneRepository(Protocol):
    """Scene units and their revisions (ADR-015 §3, §6.4). Read access for
    everyone; ``register_revision`` is for the Scene registrar only, and it
    only adds rows (``sceneops_db.repositories.revisions``)."""

    async def get(self, scene_id: str) -> SceneRecord | None: ...

    async def list(
        self,
        *,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]: ...

    async def get_revision(
        self, *, scene_id: str, manifest_checksum: str
    ) -> SceneRevisionRecord | None: ...

    async def list_revisions(self, scene_id: str) -> list[SceneRevisionRecord]: ...

    async def get_revisions(
        self, keys: Sequence[tuple[str, str]]
    ) -> list[SceneRevisionRecord]:
        """The registered revisions among ``(scene_id, manifest_checksum)``
        keys, in key order; a key that names no revision is absent."""
        ...

    async def list_source_revisions(
        self, *, source_kind: str, source_id: str, producer_fingerprint: str
    ) -> list[SceneRevisionRecord]:
        """Every revision of a unit sourced from ``(source_kind, source_id)``
        registered under ``producer_fingerprint``: at most one per unit.
        Scene units are recording-sourced, so a content source has none."""
        ...

    async def register_revision(
        self, revision: SceneRevisionRecord
    ) -> RevisionRegistration[SceneRevisionRecord]: ...

    # --- the revisions a DatasetVersion pins ----------------------------------

    async def get_member_revision(
        self, *, dataset_id: str, dataset_version: str, scene_id: str
    ) -> SceneRevisionRecord | None: ...

    async def list_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRevisionRecord]: ...


@runtime_checkable
class SceneRunRepository(Protocol):
    async def create(self, run: SceneRunRecord) -> SceneRunRecord: ...

    async def get(self, run_id: str) -> SceneRunRecord | None: ...

    async def update(self, run: SceneRunRecord) -> SceneRunRecord: ...

    async def list(
        self,
        *,
        type: RunType | None = None,
        status: RunStatus | None = None,
        scene_id: str | None = None,
        manifest_artifact_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRunRecord]: ...

    async def latest_succeeded_for_member_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        run_type: RunType,
    ) -> dict[str, SceneRunRecord]: ...
