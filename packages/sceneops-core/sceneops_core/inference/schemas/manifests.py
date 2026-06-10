from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel


class DetectionPredictionShardRef(SceneOpsBaseModel):
    """Reference to one prediction shard artifact."""

    scene_id: str | None = None
    sample_id: str | None = None
    uri: str
    prediction_count: int = 0
    metadata: JsonDict = Field(default_factory=dict)


class DetectionPredictionManifest(SceneOpsBaseModel):
    """Run-level prediction manifest artifact.

    Stored at:
      runs/inference/{inference_run_id}/prediction_manifest.json

    This is the artifact consumed by evaluate_detection.
    It is intentionally separate from:
      runs/inference/{inference_run_id}/run.json
    """

    inference_run_id: str

    dataset_id: str
    dataset_version: str

    model_id: str | None = None
    model_version: str | None = None
    inference_backend: str | None = None

    status: str = "succeeded"

    scene_count: int = 0
    sample_count: int = 0
    inference_request_count: int = 0

    prediction_count: int = 0
    evaluable_prediction_count: int = 0
    lifting_succeeded_count: int = 0
    lifting_failed_count: int = 0
    lifting_not_applicable_count: int = 0

    predictions_root_uri: str | None = None
    prediction_manifest_uri: str | None = None

    # Current implementation may store per-sample files:
    #   predictions/{sample_id}.json
    prediction_shards: list[DetectionPredictionShardRef] = Field(default_factory=list)

    metrics: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)

    created_at: datetime | None = None
