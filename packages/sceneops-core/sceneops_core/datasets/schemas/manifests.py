from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import DatasetManifestStatus


class DatasetSceneIndexEntry(SceneOpsBaseModel):
    scene_id: str
    scene_manifest_uri: str

    sample_count: int = 0
    frame_count: int = 0
    channels: list[str] = Field(default_factory=list)

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

    metadata: JsonDict = Field(default_factory=dict)
