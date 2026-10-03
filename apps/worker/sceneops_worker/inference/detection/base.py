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
from sceneops_worker.scenes.keyframes import KeyframeObservation
from sceneops_worker.scenes.payloads import ArtifactPayloadLocator


@dataclass(frozen=True)
class DetectionSampleInput:
    """One keyframe sample resolved for one inference request.

    ``image_uri`` / ``lidar_uri`` are where the camera / lidar payload
    artifacts live, resolved through their ArtifactRecords. The inference
    server resolves ``image_uri`` to image bytes; workers only pass it.
    ``camera`` / ``lidar`` carry the canonical observations with their
    calibration and source-associated ego pose, for frustum lifting.
    """

    dataset_id: str
    dataset_version: str
    scene_id: str
    sample_id: str
    camera_channel: str
    image_uri: str

    timestamp_ns: int | None = None
    camera: KeyframeObservation | None = None
    lidar: KeyframeObservation | None = None
    lidar_uri: str | None = None

    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DetectionInferenceRequest:
    input: DetectionInferenceInput
    scene_artifact_store: SceneArtifactStore
    run_artifact_store: RunArtifactStore
    payload_locator: ArtifactPayloadLocator | None = None


DetectionInferenceBackend: TypeAlias = InferenceBackend[
    DetectionInferenceRequest,
    DetectionInferenceResult,
]
