from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.inference.contracts import InferenceBackend
from sceneops_core.inference.schemas import (
    DetectionInferenceInput,
    DetectionInferenceResult,
)
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.runs import RunArtifactStore


@dataclass(frozen=True)
class DetectionInferenceRequest:
    input: DetectionInferenceInput
    dataset_artifact_store: DatasetArtifactStore
    run_artifact_store: RunArtifactStore


DetectionInferenceBackend: TypeAlias = InferenceBackend[
    DetectionInferenceRequest,
    DetectionInferenceResult,
]
