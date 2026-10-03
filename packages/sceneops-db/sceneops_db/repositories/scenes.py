from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from sceneops_core.provenance import UnitSourceKind
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas import SceneRecord
from sceneops_core.scenes.schemas.runs import (
    SceneProfileRunRecord,
    SceneValidationRunRecord,
)

SceneRunRecord: TypeAlias = SceneValidationRunRecord | SceneProfileRunRecord


@dataclass(frozen=True)
class SceneMembershipSummary:
    """Aggregate of one DatasetVersion's SceneRecord rows."""

    scene_count: int
    keyframe_count: int
    observation_count: int
    observed_channels: list[str]


@runtime_checkable
class SceneRepository(Protocol):
    """Read access for everyone; ``insert`` / ``replace_revision`` /
    ``delete`` are for the Scene registrar only (ADR-007 §17.5)."""

    async def get(self, scene_id: str) -> SceneRecord | None: ...

    async def list(
        self,
        *,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        source_kind: UnitSourceKind | None = None,
        external_format: str | None = None,
        robot_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRecord]: ...

    async def list_recording_scope(
        self, *, dataset_id: str, dataset_version: str, robot_run_id: str
    ) -> list[SceneRecord]: ...

    async def summarize_membership(
        self, *, dataset_id: str, dataset_version: str
    ) -> SceneMembershipSummary: ...

    async def insert(self, scene: SceneRecord) -> SceneRecord: ...

    async def replace_revision(self, scene: SceneRecord) -> SceneRecord: ...

    async def delete(self, scene_ids: list[str]) -> int: ...


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
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SceneRunRecord]: ...

    async def latest_succeeded_for_current_revisions(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        run_type: RunType,
    ) -> dict[str, SceneRunRecord]: ...
