from __future__ import annotations

from datetime import datetime

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.runs.enums import RunStatus
from sceneops_core.schemas.datasets.validation import (
    DatasetValidationScope,
    DatasetValidationStatus,
)


class InferenceRunRecord(SceneOpsBaseModel):
    id: str

    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    status: RunStatus = RunStatus.PENDING

    sample_count: int | None = None
    prediction_count: int | None = None

    run_manifest_uri: str | None = None
    predictions_root_uri: str | None = None

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    job_id: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)
    error: JsonDict | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class EvaluationRunRecord(SceneOpsBaseModel):
    id: str

    inference_run_id: str

    dataset_id: str
    dataset_version: str

    model_id: str
    model_version: str

    evaluator_id: str = "center-distance"
    status: RunStatus = RunStatus.PENDING

    sample_count: int | None = None

    evaluation_manifest_uri: str | None = None
    samples_root_uri: str | None = None

    metrics: JsonDict = Field(default_factory=dict)
    class_metrics: JsonDict = Field(default_factory=dict)

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    job_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
    error: JsonDict | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DatasetValidationRunRecord(SceneOpsBaseModel):
    id: str

    dataset_id: str
    dataset_version: str

    status: RunStatus = RunStatus.PENDING
    validation_status: DatasetValidationStatus | None = None
    should_block_pipeline: bool = False

    dataset_manifest_uri: str | None = None
    validation_report_uri: str | None = None

    scope: DatasetValidationScope | None = None
    max_samples: int | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    validated_scene_count: int | None = None
    validated_sample_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    missing_scene_count: int | None = None
    missing_sample_count: int | None = None
    missing_channel_count: int | None = None
    missing_artifact_count: int | None = None

    pipeline_run_id: str | None = None
    pipeline_step_run_id: str | None = None
    job_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)
    error: JsonDict | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
