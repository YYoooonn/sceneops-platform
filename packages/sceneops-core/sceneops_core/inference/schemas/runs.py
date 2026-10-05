from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType


class InferenceRunRecord(BaseRunRecord):
    type: RunType = RunType.INFERENCE

    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    inference_backend: str = "mock"

    predictions_root_uri: str | None = None
    prediction_manifest_uri: str | None = None
    # Pins the published prediction manifest revision (ADR-007 §33.5).
    prediction_manifest_checksum: str | None = None

    sample_count: int | None = None
    prediction_count: int | None = None

    metrics: JsonDict = Field(default_factory=dict)
