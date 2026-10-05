from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.inference.schemas.manifests import PredictionRevisionRef
from sceneops_core.labels.schemas import LabelSetRef
from sceneops_core.sample_views.schemas import SampleViewRef
from sceneops_core.scenarios.schemas.manifests import ScenarioSetRef


class EvaluationInputs(SceneOpsBaseModel):
    """The exact revisions an evaluation scored (ADR-007 §33.5): the
    prediction manifest revision, the label set revision that defines
    ground truth, and the sample view revisions whose samples were
    compared. Nothing here is resolved at read time: re-running the
    evaluation with these pins reads the same data."""

    prediction: PredictionRevisionRef
    label_set: LabelSetRef
    sample_views: list[SampleViewRef] = Field(default_factory=list)
    scenario_set: ScenarioSetRef | None = None


class DetectionEvaluationManifest(SceneOpsBaseModel):
    """Typed schema for the evaluation run manifest artifact (evaluation.json)."""

    evaluation_run_id: str
    inference_run_id: str | None = None

    dataset_id: str
    dataset_version: str

    model_id: str | None = None
    model_version: str | None = None

    inputs: EvaluationInputs | None = None

    status: str = "succeeded"
    match_distance_m: float | None = None

    sample_count: int | None = None
    prediction_count: int | None = None
    evaluable_prediction_count: int | None = None
    lifting_failed_prediction_count: int | None = None
    ground_truth_count: int | None = None
    evaluation_unit: str | None = None

    primary_metric_name: str | None = None
    primary_metric_value: float | None = None

    evaluation_manifest_uri: str | None = None
    metrics_uri: str | None = None
    samples_root_uri: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
    class_metrics: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
