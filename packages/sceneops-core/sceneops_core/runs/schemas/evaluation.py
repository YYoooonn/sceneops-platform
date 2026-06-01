from __future__ import annotations

from pydantic import Field
from sceneops_core.common.schemas import JsonDict

from .base import BaseRunRecord


class EvaluationRunRecord(BaseRunRecord):
    inference_run_id: str

    dataset_id: str
    dataset_version: str
    model_id: str
    model_version: str

    evaluator_id: str = "center-distance"

    sample_count: int | None = None
    evaluation_manifest_uri: str | None = None
    samples_root_uri: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
    class_metrics: JsonDict = Field(default_factory=dict)
