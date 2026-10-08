from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from sceneops_core.evaluations.contracts import Evaluator
from sceneops_core.evaluations.schemas import EvaluationInputs
from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_core.inference.schemas.manifests import DetectionPredictionManifest
from sceneops_core.jobs.schemas.params import MissingGroundTruthPolicy
from sceneops_core.labels import LabelSetManifest
from sceneops_core.sample_views import SceneSampleViewManifest

from sceneops_derived import RunArtifactStore

DEFAULT_MATCH_DISTANCE_M = 2.0


@dataclass(frozen=True)
class DetectionEvaluationRequest:
    """Everything an evaluator reads, already resolved from pinned revisions.

    ``inputs`` is the pin set recorded into the evaluation manifest;
    ``prediction``, ``label_set`` and ``views`` are exactly what those pins
    resolve to, so an evaluator never looks anything up.
    """

    evaluation_run_id: str
    inference_run_id: str
    dataset_id: str
    dataset_version: str
    inputs: EvaluationInputs
    prediction: DetectionPredictionManifest
    label_set: LabelSetManifest
    # Pinned views, keyed by scene id.
    views: dict[str, SceneSampleViewManifest]
    run_artifact_store: RunArtifactStore
    match_distance_m: float = DEFAULT_MATCH_DISTANCE_M
    # Exact category allowlist; None evaluates every category.
    categories: frozenset[str] | None = None
    missing_gt_policy: MissingGroundTruthPolicy = MissingGroundTruthPolicy.SKIP


DetectionEvaluationResult: TypeAlias = DetectionEvaluationManifest

DetectionEvaluator: TypeAlias = Evaluator[
    DetectionEvaluationRequest,
    DetectionEvaluationResult,
]
