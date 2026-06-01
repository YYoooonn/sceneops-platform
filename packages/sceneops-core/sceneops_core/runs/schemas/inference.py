from __future__ import annotations

from pydantic import Field
from sceneops_core.common.schemas import JsonDict
from .base import BaseRunRecord


class InferenceRunRecord(BaseRunRecord):
    dataset_id: str
    dataset_version: str
    model_id: str
    model_version: str

    sample_count: int | None = None
    prediction_count: int | None = None

    run_manifest_uri: str | None = None
    predictions_root_uri: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
