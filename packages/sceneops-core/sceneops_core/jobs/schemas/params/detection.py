from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from sceneops_core.common.schemas import JsonDict
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.labels.schemas import LabelSetRef
from sceneops_core.sample_views.schemas import SampleViewRef

from .base import BaseJobParams


class PredictDetectionJobParams(BaseJobParams):
    """Detection over explicit derived inputs (ADR-007 §33.5).

    The input is either a ScenarioSet (its pinned member views and selected
    samples) or an explicit list of pinned SceneSampleView revisions, never
    a DatasetVersion scan. Channel names are always explicit: they belong
    to the recording, not to the platform.
    """

    # The DatasetVersion the views belong to; scope only.
    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    inference_backend: InferenceBackendType = InferenceBackendType.MOCK
    inference_run_id: str | None = None

    scenario_set_id: str | None = None
    sample_views: list[SampleViewRef] = Field(default_factory=list)

    # The channel whose observation is the detector's image.
    camera_channel: str = Field(min_length=1)
    # The channel whose point cloud lifts 2-D boxes to 3-D; None disables lifting.
    lidar_channel: str | None = None

    detection_prompt: str | None = None
    box_threshold: float | None = None
    text_threshold: float | None = None
    max_image_size: int | None = None

    max_samples: int | None = Field(default=None, ge=1)

    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _exactly_one_input_source(self) -> PredictDetectionJobParams:
        if (self.scenario_set_id is None) == (not self.sample_views):
            raise ValueError("provide exactly one of scenario_set_id or sample_views")
        return self


class MissingGroundTruthPolicy(StrEnum):
    """What an evaluation does with a predicted sample the label set does not
    cover: ``skip`` records it as skipped, ``fail`` fails the evaluation."""

    FAIL = "fail"
    SKIP = "skip"


class EvaluateDetectionJobParams(BaseJobParams):
    """Score one prediction revision against one label set revision.

    ``label_set`` is always an explicit pin. ``prediction_manifest_checksum``
    pins the prediction revision; unset, the job resolves the revision the
    inference run recorded and writes it into the evaluation's inputs.
    """

    dataset_id: str
    dataset_version: str

    inference_run_id: str
    prediction_manifest_checksum: str | None = None
    label_set: LabelSetRef

    evaluation_run_id: str | None = None

    evaluator_id: str = "center-distance"
    match_distance_m: float = 2.0
    # Exact category allowlist; None evaluates every category.
    categories: list[str] | None = None

    missing_gt_policy: MissingGroundTruthPolicy = MissingGroundTruthPolicy.SKIP

    metadata: JsonDict = Field(default_factory=dict)


__all__ = [
    "EvaluateDetectionJobParams",
    "MissingGroundTruthPolicy",
    "PredictDetectionJobParams",
]
