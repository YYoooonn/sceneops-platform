from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.inference.contracts import InferenceBackend
from sceneops_core.inference.schemas import (
    DetectionInferenceInput,
    DetectionInferenceResult,
)
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore


@dataclass(frozen=True)
class DetectionInferenceRequest:
    input: DetectionInferenceInput
    scene_artifact_store: SceneArtifactStore
    run_artifact_store: RunArtifactStore


DetectionInferenceBackend: TypeAlias = InferenceBackend[
    DetectionInferenceRequest,
    DetectionInferenceResult,
]
