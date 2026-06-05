from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class DetectionPredictionManifest(SceneOpsBaseModel):
    """Typed schema for the inference run manifest artifact (run.json)."""

    inference_run_id: str

    dataset_id: str
    dataset_version: str

    model_id: str | None = None
    model_version: str | None = None
    inference_backend: str | None = None

    status: str = "succeeded"

    sample_count: int = 0
    prediction_count: int = 0

    predictions_root_uri: str | None = None
    prediction_manifest_uri: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
