from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.evaluations.contracts import Evaluator
from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore

DEFAULT_MATCH_DISTANCE_M = 2.0


@dataclass(frozen=True)
class DetectionEvaluationRequest:
    dataset_manifest: DatasetManifest
    scene_artifact_store: SceneArtifactStore
    run_artifact_store: RunArtifactStore
    inference_run_id: str
    evaluation_run_id: str
    match_distance_m: float = DEFAULT_MATCH_DISTANCE_M


DetectionEvaluationResult: TypeAlias = DetectionEvaluationManifest

DetectionEvaluator: TypeAlias = Evaluator[
    DetectionEvaluationRequest,
    DetectionEvaluationResult,
]
