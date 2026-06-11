from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors import EgoPoseManifest, SensorModality
from sceneops_core.sensors.manifests import (
    ImageMetadataManifest,
    SensorCalibrationManifest,
)

from .enums import SceneAssetKind, SceneGenerationMethod, SceneOriginType
from .world_state import WorldStateManifest


class SceneLineage(SceneOpsBaseModel):
    raw_log_id: str | None = None
    segment_id: str | None = None

    source_dataset_id: str | None = None
    source_dataset_version: str | None = None
    source_scene_id: str | None = None

    parent_scene_ids: list[str] = Field(default_factory=list)
    transformation_ids: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class SceneGenerationMetadata(SceneOpsBaseModel):
    origin_type: SceneOriginType = SceneOriginType.REAL
    generation_method: SceneGenerationMethod = SceneGenerationMethod.UNKNOWN

    generator_name: str | None = None
    generator_version: str | None = None

    prompt: str | None = None
    params: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)


class SceneAssetRef(SceneOpsBaseModel):
    asset_id: str
    kind: SceneAssetKind

    uri: str
    format: str | None = None

    node_id: str | None = None
    sample_id: str | None = None
    channel: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class SceneAnnotationManifest(SceneOpsBaseModel):
    annotation_id: str
    sample_id: str

    source_annotation_id: str | None = None

    category: str | None = None
    instance_id: str | None = None

    translation: list[float] = Field(default_factory=list)
    size: list[float] = Field(default_factory=list)
    rotation: list[float] = Field(default_factory=list)

    velocity: list[float] | None = None

    metadata: JsonDict = Field(default_factory=dict)


class SceneSensorFrameManifest(SceneOpsBaseModel):
    """A single sensor observation within a sample.

    Calibration and ego-pose are stored in the scene-level registries
    (SceneManifest.calibrated_sensors / .ego_poses) and referenced here by ID.
    Image metadata remains frame-local.
    """

    frame_id: str
    sample_id: str

    timestamp_us: int
    channel: str
    modality: SensorModality = SensorModality.UNKNOWN

    uri: str

    calibration_id: str | None = None
    ego_pose_id: str | None = None

    image: ImageMetadataManifest | None = None

    annotation_ids: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class SceneSampleManifest(SceneOpsBaseModel):
    """A keyframe grouping of sensor frames within a scene."""

    sample_id: str
    scene_id: str

    timestamp_us: int
    frame_index: int | None = None

    sensor_frames: list[SceneSensorFrameManifest] = Field(default_factory=list)
    annotations: list[SceneAnnotationManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class SceneManifest(SceneOpsBaseModel):
    """Full scene representation.

    calibrated_sensors: scene-level registry of sensor calibration records,
      deduplicated by calibration_id.  Frames reference these by calibrated_sensor_id.

    ego_poses: scene-level time-varying ego-pose records,
      referenced by frames via ego_pose_id.
    """

    scene_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    lineage: SceneLineage = Field(default_factory=SceneLineage)
    generation: SceneGenerationMetadata = Field(default_factory=SceneGenerationMetadata)

    # Scene-level registries
    calibrated_sensors: list[SensorCalibrationManifest] = Field(default_factory=list)
    ego_poses: list[EgoPoseManifest] = Field(default_factory=list)

    samples: list[SceneSampleManifest] = Field(default_factory=list)
    assets: list[SceneAssetRef] = Field(default_factory=list)

    world_state: WorldStateManifest | None = None
    world_state_uri: str | None = None

    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None

    sample_count: int = 0
    frame_count: int = 0
    channels: list[str] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)
