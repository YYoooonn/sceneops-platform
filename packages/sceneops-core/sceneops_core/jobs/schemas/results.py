from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from sceneops_core.datasets.schemas import (
    DatasetType,
    DatasetValidationScope,
    DatasetValidationStatus
)
from .enums import JobType

class BaseJobResult(SceneOpsBaseModel):
    dataset_id: str
    dataset_version: str

    result_summary: JsonDict = Field(default_factory=dict)


class IngestDatasetJobResult(BaseJobResult):
    dataset_type: DatasetType

    dataset_manifest_uri: str
    scene_count: int | None = None
    sample_count: int


class ValidateDatasetJobResult(BaseJobResult):
    dataset_manifest_uri: str

    validation_run_id: str
    validation_report_uri: str

    status: DatasetValidationStatus
    validation_scope: DatasetValidationScope
    should_block_pipeline: bool
    decision_reason: str | None = None

    scene_count: int
    sample_count: int
    annotation_count: int = 0

    validated_scene_count: int = 0
    validated_sample_count: int = 0

    issue_count: int = 0
    error_count: int = 0
    warning_count: int = 0

    missing_scene_count: int = 0
    missing_sample_count: int = 0
    missing_channel_count: int = 0
    missing_artifact_count: int = 0


class ProfileDatasetJobResult(BaseJobResult):
    dataset_manifest_uri: str

    profile_run_id: str
    profile_report_uri: str

    scene_count: int
    sample_count: int
    annotation_count: int = 0

    profiled_scene_count: int = 0
    profiled_sample_count: int = 0

    observed_channels: list[str] = Field(default_factory=list)

    missing_required_channel_count: int = 0
    sensor_coverage_ratio: float = 0.0

    empty_annotation_sample_count: int = 0
    empty_annotation_sample_ratio: float = 0.0


class PredictDetectionJobResult(BaseJobResult):
    model_id: str
    model_version: str

    inference_run_id: str
    prediction_manifest_uri: str

    sample_count: int


class EvaluateDetectionJobResult(BaseJobResult):
    inference_run_id: str
    evaluation_run_id: str
    evaluation_manifest_uri: str

    metrics: JsonDict = Field(default_factory=dict)
    sample_count: int | None = None


JobResult = (
    IngestDatasetJobResult
    | ValidateDatasetJobResult
    | ProfileDatasetJobResult
    | PredictDetectionJobResult
    | EvaluateDetectionJobResult
)


def parse_job_result(job_type: JobType, result: JsonDict) -> JobResult:
    if job_type == JobType.INGEST_DATASET:
        return IngestDatasetJobResult.model_validate(result)

    if job_type == JobType.PROFILE_DATASET:
        return ProfileDatasetJobResult.model_validate(result)

    if job_type == JobType.VALIDATE_DATASET:
        return ValidateDatasetJobResult.model_validate(result)

    if job_type == JobType.PREDICT_DETECTION:
        return PredictDetectionJobResult.model_validate(result)

    if job_type == JobType.EVALUATE_DETECTION:
        return EvaluateDetectionJobResult.model_validate(result)

    raise ValueError(f"Unsupported job type: {job_type}")
