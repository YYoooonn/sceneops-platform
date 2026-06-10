from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from sceneops_core.inference.contracts import InferenceBackend
from sceneops_core.inference.schemas import (
    DetectionInferenceInput,
    DetectionInferenceResult,
)
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore


@dataclass(frozen=True)
class DetectionSampleInput:
    """Resolved sample ready for one inference request.

    ``image_uri`` is the primary image location (file:// or future remote).
    The inference server resolves this URI to the actual image bytes.
    Workers must not read the image themselves — they only construct and pass
    the URI.
    """

    dataset_id: str
    dataset_version: str
    scene_id: str
    sample_id: str
    camera_channel: str
    image_uri: str  # file:// URI (or future s3://, gs://)

    timestamp_us: int | None = None
    lidar_uri: str | None = None  # for 3D frustum lifting
    # Raw sensor frame objects for frustum lifting calibration data.
    # NOTE: frustum lifting is pending a schema migration (see frustum_lifting.py).
    camera_sensor_frame: Any | None = None  # SceneSensorFrameManifest
    lidar_sensor_frame: Any | None = None  # SceneSensorFrameManifest
    scene_manifest_uri: str | None = None
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
