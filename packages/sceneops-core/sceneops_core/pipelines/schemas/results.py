from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import ErrorInfo, JsonDict, SceneOpsBaseModel
from sceneops_core.jobs.schemas import JobStatus, JobType


class PipelineLineageEdge(SceneOpsBaseModel):
    from_pipeline_step_id: str
    from_output: str | None = None

    to_pipeline_step_id: str | None = None
    to_input: str | None = None

    artifact_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class PipelineLineage(SceneOpsBaseModel):
    sources: list[JsonDict] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    edges: list[PipelineLineageEdge] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class PipelineStepResult(SceneOpsBaseModel):
    pipeline_step_id: str
    pipeline_step_name: str

    job_type: JobType
    job_id: str | None = None

    status: JobStatus

    result: JsonDict | None = None
    error: ErrorInfo | None = None

    produced_artifacts: dict[str, str] = Field(default_factory=dict)
    consumed_artifacts: dict[str, str] = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)


class PipelineRunResult(SceneOpsBaseModel):
    summary: JsonDict = Field(default_factory=dict)
    lineage: PipelineLineage = Field(default_factory=PipelineLineage)
    outputs: dict[str, JsonDict] = Field(default_factory=dict)

    steps: list[PipelineStepResult] = Field(default_factory=list)
