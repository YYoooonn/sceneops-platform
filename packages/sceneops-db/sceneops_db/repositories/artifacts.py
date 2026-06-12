from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef, ArtifactRecord


@runtime_checkable
class ArtifactRepository(Protocol):
    async def create(
        self,
        *,
        artifact_id: str,
        ref: ArtifactRef,
        backend: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        scenario_set_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> ArtifactRecord: ...

    async def get(self, artifact_id: str) -> ArtifactRecord | None: ...

    async def list(
        self,
        *,
        kind: ArtifactKind | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        scenario_set_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArtifactRecord]: ...
