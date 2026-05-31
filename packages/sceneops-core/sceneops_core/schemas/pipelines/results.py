from __future__ import annotations

from pydantic import Field

from sceneops_core.schemas.base import SceneOpsBaseModel
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.pipelines.enums import PipelineRunStatus


class PipelineResultSummary(SceneOpsBaseModel):
    status: PipelineRunStatus

    dataset_status: str | None = None
    validation_status: str | None = None
    should_block_pipeline: bool | None = None

    scene_count: int | None = None
    sample_count: int | None = None
    annotation_count: int | None = None

    validated_scene_count: int | None = None
    validated_sample_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    metrics: JsonDict | None = None


class PipelineResultLineage(SceneOpsBaseModel):
    dataset_id: str | None = None
    dataset_version: str | None = None
    model_id: str | None = None
    model_version: str | None = None

    dataset_manifest_uri: str | None = None

    validation_run_id: str | None = None
    validation_report_uri: str | None = None

    inference_run_id: str | None = None
    prediction_manifest_uri: str | None = None

    evaluation_run_id: str | None = None
    evaluation_manifest_uri: str | None = None


class PipelineDatasetOutput(SceneOpsBaseModel):
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

    validated_scene_count: int | None = None
    validated_sample_count: int | None = None

    issue_count: int | None = None
    error_count: int | None = None
    warning_count: int | None = None

    missing_scene_count: int | None = None
    missing_sample_count: int | None = None
    missing_channel_count: int | None = None
    missing_artifact_count: int | None = None


class PipelineInferenceOutput(SceneOpsBaseModel):
    run_id: str | None = None
    prediction_manifest_uri: str | None = None


class PipelineEvaluationOutput(SceneOpsBaseModel):
    run_id: str | None = None
    evaluation_manifest_uri: str | None = None
    metrics: JsonDict | None = None


class PipelineResultOutputs(SceneOpsBaseModel):
    dataset: PipelineDatasetOutput | None = None
    validation: PipelineValidationOutput | None = None
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
