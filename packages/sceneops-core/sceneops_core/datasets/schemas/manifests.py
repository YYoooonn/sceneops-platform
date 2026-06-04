from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import DatasetManifestStatus, DatasetSplit


class DatasetSceneIndexEntry(SceneOpsBaseModel):
    scene_id: str

    scene_manifest_uri: str

    split: DatasetSplit = DatasetSplit.UNASSIGNED

    sample_count: int = 0
    frame_count: int = 0
    channels: list[str] = Field(default_factory=list)

    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None

    raw_log_id: str | None = None
    segment_id: str | None = None

    tags: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class DatasetManifest(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    status: DatasetManifestStatus = DatasetManifestStatus.READY

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0

    channels: list[str] = Field(default_factory=list)

    scenes: list[DatasetSceneIndexEntry] = Field(default_factory=list)

    validation_summary: JsonDict = Field(default_factory=dict)
    profile_summary: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None

    metadata: JsonDict = Field(default_factory=dict)
