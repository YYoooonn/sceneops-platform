from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias

from sceneops_core.inference.contracts import InferenceBackend
from sceneops_core.inference.schemas import (
    DetectionInferenceInput,
    DetectionInferenceResult,
)
from sceneops_core.sensors.manifests import SensorCalibrationManifest, EgoPoseManifest
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore


@dataclass(frozen=True)
class DetectionSampleInput:
    """Resolved sample ready for one inference request.

    ``image_uri`` is the primary image location (file:// or future remote).
    The inference server resolves this URI to the actual image bytes.
    Workers must not read the image themselves — they only construct and pass
    the URI.

    calibrated_sensor_index / ego_pose_index:
        Scene-level lookup tables built from SceneManifest.calibrated_sensors
        and .ego_poses. Used by frustum lifting to resolve frame ID references
        without embedding inline objects in the persisted manifest.
    """

    dataset_id: str
    dataset_version: str
    scene_id: str
    sample_id: str
    camera_channel: str
    image_uri: str  # file:// URI (or future s3://, gs://)

    timestamp_us: int | None = None
    lidar_uri: str | None = None
    camera_sensor_frame: Any | None = None  # SceneSensorFrameManifest
    lidar_sensor_frame: Any | None = None  # SceneSensorFrameManifest
    scene_manifest_uri: str | None = None

    # Scene-level registry indexes for lifting (not persisted to manifests)
    calibrated_sensor_index: dict[str, SensorCalibrationManifest] = field(
        default_factory=dict
    )
    ego_pose_index: dict[str, EgoPoseManifest] = field(default_factory=dict)

    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DetectionInferenceRequest:
    input: DetectionInferenceInput
    scene_artifact_store: SceneArtifactStore
    run_artifact_store: RunArtifactStore


DetectionInferenceBackend: TypeAlias = InferenceBackend[
    DetectionInferenceRequest,
    DetectionInferenceResult,
]
