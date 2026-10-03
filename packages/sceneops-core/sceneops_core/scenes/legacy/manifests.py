"""Pre-canonical, sample-centric Scene manifests.

Produced only by the two Scene producers that cannot yet satisfy the
canonical ``SceneManifest`` contract (``sceneops_core.scenes.schemas``): the
nuScenes scene integration and the raw-log scene builder. Both reference
payloads relative to an external source root, keep observations only when a
sampling step associates them with a sample, and carry untyped provenance.

These manifests are not canonical units. They are stored as
``ArtifactKind.LEGACY_SCENE_MANIFEST`` and canonical registration
(``REGISTER_SCENES``) does not accept them, so nothing downstream of
registration ever reads this shape.
"""

from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.sensors import EgoPoseManifest, SensorModality
from sceneops_core.sensors.manifests import (
    ImageMetadataManifest,
    SensorCalibrationManifest,
)


class LegacySceneAnnotationManifest(SceneOpsBaseModel):
    annotation_id: str
    sample_id: str

    source_annotation_id: str | None = None
    source_sample_id: str | None = None

    category: str | None = None
    instance_id: str | None = None

    timestamp_us: int | None = None

    coordinate_frame: str = "world"

    translation: list[float] = Field(default_factory=list)
    size: list[float] = Field(default_factory=list)
    rotation: list[float] = Field(default_factory=list)
    rotation_format: str = "quaternion_wxyz"

    velocity: list[float] | None = None

    attributes: list[str] = Field(default_factory=list)

    num_lidar_points: int | None = None
    num_radar_points: int | None = None

    metadata: JsonDict = Field(default_factory=dict)


class LegacySceneSensorFrameManifest(SceneOpsBaseModel):
    """One sensor frame associated with a sample. ``uri`` is relative to the
    producer's source root."""

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


class LegacySceneSampleManifest(SceneOpsBaseModel):
    sample_id: str
    scene_id: str

    timestamp_us: int
    frame_index: int | None = None

    sensor_frames: list[LegacySceneSensorFrameManifest] = Field(default_factory=list)
    annotations: list[LegacySceneAnnotationManifest] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class LegacySceneManifest(SceneOpsBaseModel):
    scene_id: str

    dataset_id: str | None = None
    dataset_version: str | None = None

    calibrated_sensors: list[SensorCalibrationManifest] = Field(default_factory=list)
    ego_poses: list[EgoPoseManifest] = Field(default_factory=list)

    samples: list[LegacySceneSampleManifest] = Field(default_factory=list)

    start_timestamp_us: int | None = None
    end_timestamp_us: int | None = None

    sample_count: int = 0
    frame_count: int = 0
    annotation_count: int = 0

    channels: list[str] = Field(default_factory=list)

    has_ground_truth: bool = False
    ground_truth_source: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


__all__ = [
    "LegacySceneAnnotationManifest",
    "LegacySceneManifest",
    "LegacySceneSampleManifest",
    "LegacySceneSensorFrameManifest",
]
