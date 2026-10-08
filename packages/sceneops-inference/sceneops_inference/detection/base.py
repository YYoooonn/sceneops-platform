from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from sceneops_core.inference.contracts import InferenceBackend
from sceneops_core.inference.schemas import (
    DetectionInferenceInput,
    DetectionInferenceResult,
)
from sceneops_core.labels import Box3DLabel
from sceneops_core.sample_views import ResolvedMember
from sceneops_core.scenes.schemas.manifests import ScenePose
from sceneops_storage import ArtifactStore

from sceneops_derived import DerivedManifestStore
from sceneops_derived import RunArtifactStore


@dataclass(frozen=True)
class DetectionSampleInput:
    """One sample of a pinned SceneSampleView, resolved for inference.

    ``image_uri`` / ``lidar_uri`` are where the camera / lidar payload
    artifacts live, resolved through their verified ArtifactRecords. The
    inference server resolves ``image_uri`` to image bytes; workers only
    pass it. ``camera`` / ``lidar`` carry the canonical observations with
    their calibration, and ``pose`` is the ego pose the view associated
    with the sample, for 3-D lifting.

    ``reference_labels`` is filled only for a backend that declares
    ``uses_reference_labels`` (a test double that perturbs labels). Real
    backends never see labels.
    """

    scene_id: str
    sample_id: str
    camera_channel: str
    image_uri: str
    camera: ResolvedMember

    lidar: ResolvedMember | None = None
    lidar_uri: str | None = None
    pose: ScenePose | None = None
    reference_labels: list[Box3DLabel] = field(default_factory=list)


@dataclass(frozen=True)
class DetectionInferenceRequest:
    input: DetectionInferenceInput
    samples: list[DetectionSampleInput]
    artifact_store: ArtifactStore
    run_artifact_store: RunArtifactStore
    derived_store: DerivedManifestStore


@dataclass(frozen=True)
class SamplePrediction:
    sample: DetectionSampleInput
    predictions: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendRun:
    """What a backend produced: per-sample predictions and run-level metrics."""

    samples: list[SamplePrediction]
    metrics: dict[str, Any] = field(default_factory=dict)
    inference_request_count: int | None = None


DetectionInferenceBackend: TypeAlias = InferenceBackend[
    DetectionInferenceRequest,
    DetectionInferenceResult,
]


class SampleDetectionBackend(ABC):
    """A backend computes predictions; publication of the pinned prediction
    manifest is shared (``publication.publish_predictions``)."""

    uses_reference_labels: bool = False

    @property
    @abstractmethod
    def backend_type(self) -> str: ...

    @abstractmethod
    async def predict(self, request: DetectionInferenceRequest) -> BackendRun: ...

    async def run(self, request: DetectionInferenceRequest) -> DetectionInferenceResult:
        from sceneops_inference.detection.publication import publish_predictions

        return await publish_predictions(request, await self.predict(request))
