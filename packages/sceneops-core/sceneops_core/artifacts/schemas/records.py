from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict


class ArtifactRecord(SceneOpsBaseModel):
    artifact_id: str

    kind: str
    uri: str

    backend: str | None = None

    owner_type: str | None = None
    owner_id: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None
    scene_id: str | None = None
    scenario_set_id: str | None = None

    run_id: str | None = None
    job_id: str | None = None
    pipeline_run_id: str | None = None

    media_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None

    created_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
