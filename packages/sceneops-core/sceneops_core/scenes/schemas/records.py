from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import SceneGenerationMethod, SceneOriginType, SceneStatus


class SceneRecord(SceneOpsBaseModel):
    scene_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    raw_log_id: str | None = None
    segment_id: str | None = None

    status: SceneStatus = SceneStatus.CREATED
    origin_type: SceneOriginType = SceneOriginType.REAL
    generation_method: SceneGenerationMethod = SceneGenerationMethod.UNKNOWN

    scene_manifest_uri: str | None = None

    sample_count: int = 0
    frame_count: int = 0
    annotation_count: int = 0
    channels: list[str] = Field(default_factory=list)

    has_ground_truth: bool = False
    ground_truth_source: str | None = None

    started_at: datetime | None = None
    ended_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)


class SceneSampleRecord(SceneOpsBaseModel):
    sample_id: str
    scene_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    timestamp_us: int | None = None
    frame_index: int | None = None

    channels: list[str] = Field(default_factory=list)
    sample_manifest_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
