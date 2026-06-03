from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import SceneOpsBaseModel, JsonDict
from .enums import PipelineRunStatus


class PipelineResultSummary(SceneOpsBaseModel):
    status: PipelineRunStatus

    # Dataset latest state
    dataset_status: str | None = None

    # Dataset quality gate
    validation_status: str | None = None
    should_block_pipeline: bool | None = None

    # Dataset base counts
    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    # Validation counts
    validated_scene_count: int | None = None
    validated_sample_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    # Profile counts / quality signals
    profiled_scene_count: int | None = None
    profiled_sample_count: int | None = None
    observed_channel_count: int | None = None
    sensor_coverage_ratio: float | None = None
    empty_annotation_sample_ratio: float | None = None

    # Model/evaluation metrics
    metrics: JsonDict | None = None


class PipelineResultLineage(SceneOpsBaseModel):
    # Dataset lineage
    dataset_id: str | None = None
    dataset_version: str | None = None
    dataset_type: str | None = None
    dataset_manifest_uri: str | None = None

    # Dataset quality lineage
    validation_run_id: str | None = None
    validation_report_uri: str | None = None

    profile_run_id: str | None = None
    profile_report_uri: str | None = None

    # Model lineage
    model_id: str | None = None
    model_version: str | None = None

    # Inference/evaluation lineage
    inference_run_id: str | None = None
    prediction_manifest_uri: str | None = None

    evaluation_run_id: str | None = None
    evaluation_manifest_uri: str | None = None


class PipelineDatasetOutput(SceneOpsBaseModel):
    dataset_id: str | None = None
    dataset_version: str | None = None
    dataset_type: str | None = None

    manifest_uri: str | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None


class PipelineValidationOutput(SceneOpsBaseModel):
    run_id: str | None = None
    status: str | None = None
    scope: str | None = None
    report_uri: str | None = None

    should_block_pipeline: bool | None = None
    decision_reason: str | None = None

    validated_scene_count: int | None = None
    validated_sample_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    missing_scene_count: int | None = None
    missing_sample_count: int | None = None
    missing_channel_count: int | None = None
    missing_artifact_count: int | None = None


class PipelineProfileOutput(SceneOpsBaseModel):
    run_id: str | None = None
    scope: str | None = None
    report_uri: str | None = None

    profiled_scene_count: int | None = None
    profiled_sample_count: int | None = None

    observed_channels: list[str] = Field(default_factory=list)
    observed_channel_count: int | None = None

    missing_required_channel_count: int | None = None
    sensor_coverage_ratio: float | None = None

    empty_annotation_sample_count: int | None = None
    empty_annotation_sample_ratio: float | None = None


class PipelineInferenceOutput(SceneOpsBaseModel):
    run_id: str | None = None
    prediction_manifest_uri: str | None = None
    predictions_root_uri: str | None = None

    sample_count: int | None = None
    prediction_count: int | None = None


class PipelineModelOutput(SceneOpsBaseModel):
    model_id: str | None = None
    model_version: str | None = None
    model_artifact_uri: str | None = None
    backend: str | None = None


class PipelineEvaluationOutput(SceneOpsBaseModel):
    run_id: str | None = None
    evaluation_manifest_uri: str | None = None
    samples_root_uri: str | None = None

    sample_count: int | None = None
    metrics: JsonDict | None = None
    class_metrics: JsonDict | None = None


class PipelineBuildScenesOutput(SceneOpsBaseModel):
    raw_log_id: str | None = None
    raw_log_manifest_uri: str | None = None
    scene_segments_uri: str | None = None
    scene_index_uri: str | None = None
    frame_count: int | None = None
    scene_count: int | None = None
    sample_count: int | None = None
    channels: list[str] = Field(default_factory=list)


class PipelineResultOutputs(SceneOpsBaseModel):
    build_scenes: PipelineBuildScenesOutput | None = None
    dataset: PipelineDatasetOutput | None = None
    validation: PipelineValidationOutput | None = None
    profile: PipelineProfileOutput | None = None

    model: PipelineModelOutput | None = None
    inference: PipelineInferenceOutput | None = None
    evaluation: PipelineEvaluationOutput | None = None


class PipelineStepResult(SceneOpsBaseModel):
    step_name: str
    job_type: str
    job_id: str | None = None
    status: str

    result: JsonDict = Field(default_factory=dict)
    error: JsonDict | None = None


class PipelineRunResult(SceneOpsBaseModel):
    summary: PipelineResultSummary = Field(default_factory=PipelineResultSummary)
    lineage: PipelineResultLineage = Field(default_factory=PipelineResultLineage)
    outputs: PipelineResultOutputs = Field(default_factory=PipelineResultOutputs)
    steps: list[PipelineStepResult] = Field(default_factory=list)
