from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict

from .enums import DatasetManifestStatus, DatasetType, SensorModality


class DatasetManifestSummary(SceneOpsBaseModel):
    scene_count: int
    sample_count: int
    annotation_count: int = Field(default=0)


class DatasetManifestChannels(SceneOpsBaseModel):
    target: list[str] = Field(default_factory=list)
    camera: list[str] = Field(default_factory=list)
    lidar: list[str] = Field(default_factory=list)
    radar: list[str] = Field(default_factory=list)


class DatasetManifestUris(SceneOpsBaseModel):
    manifest_root: str
    dataset_manifest: str
    scene_index: str
    scene_root: str
    sample_root: str
    raw_root: str | None = Field(default=None)


class DatasetIngestMetadata(SceneOpsBaseModel):
    mode: str
    max_scenes: int | None = Field(default=None)


class DatasetManifest(SceneOpsBaseModel):
    schema_version: str = "1.0"

    dataset_id: str
    dataset_version: str
    dataset_type: DatasetType | str
    source: str

    status: DatasetManifestStatus
    generated_at: datetime

    summary: DatasetManifestSummary
    channels: DatasetManifestChannels
    uris: DatasetManifestUris

    ingest: DatasetIngestMetadata | None = None
    metadata: JsonDict = Field(default_factory=dict)


class DatasetSceneIndexItem(SceneOpsBaseModel):
    scene_id: str
    scene_token: str | None = Field(default=None)

    dataset_id: str
    dataset_version: str

    source: str
    description: str | None = None

    sample_count: int
    status: DatasetManifestStatus = DatasetManifestStatus.READY

    manifest_uri: str


class DatasetSceneIndex(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str
    source: str
    scenes: list[DatasetSceneIndexItem] = Field(default_factory=list)


class DatasetSceneManifest(SceneOpsBaseModel):
    scene_id: str
    scene_token: str | None = Field(default=None)

    dataset_id: str
    dataset_version: str

    source: str
    description: str | None = None

    sample_count: int
    first_sample_token: str | None = Field(default=None)
    last_sample_token: str | None = Field(default=None)

    status: DatasetManifestStatus = DatasetManifestStatus.READY
    sample_ids: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class CalibratedSensorManifest(SceneOpsBaseModel):
    translation: list[float]
    rotation: list[float]
    camera_intrinsic: list[list[float]] | None = Field(default=None)


class EgoPoseManifest(SceneOpsBaseModel):
    translation: list[float]
    rotation: list[float]


class SampleSensorManifest(SceneOpsBaseModel):
    channel: str
    modality: SensorModality

    # Generic file reference — absolute URI or path relative to raw_root
    uri: str
    fileformat: str | None = None

    is_key_frame: bool = True
    width: int | None = None
    height: int | None = None

    # Optional calibration — populated by ingestion, None when not yet resolved
    calibrated_sensor: CalibratedSensorManifest | None = None
    ego_pose: EgoPoseManifest | None = None

    # Format-specific traceability token (nuScenes: sample_data_token, raw log: frame_id)
    source_ref: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class SampleAnnotationManifest(SceneOpsBaseModel):
    annotation_token: str
    instance_token: str
    category_name: str

    translation: list[float]
    size: list[float]
    rotation: list[float]

    num_lidar_pts: int
    num_radar_pts: int

    visibility_token: str
    attribute_tokens: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetSampleManifest(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    sample_id: str
    sample_token: str
    scene_id: str

    timestamp_us: int
    channels: list[str] = Field(default_factory=list)

    # Optional sequence links — token or sample_id depending on source format
    prev_sample_id: str | None = None
    next_sample_id: str | None = None

    sensors: dict[str, SampleSensorManifest] = Field(default_factory=dict)
    annotations: list[SampleAnnotationManifest] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)
