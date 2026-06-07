from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import ErrorInfo, JsonDict, SceneOpsBaseModel
from sceneops_core.jobs.schemas import JobStatus, JobType


class PipelineLineageEdge(SceneOpsBaseModel):
    from_pipeline_task_id: str
    from_output: str | None = None

    to_pipeline_task_id: str | None = None
    to_input: str | None = None

    artifact_uri: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class PipelineLineage(SceneOpsBaseModel):
    sources: list[JsonDict] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    edges: list[PipelineLineageEdge] = Field(default_factory=list)

    metadata: JsonDict = Field(default_factory=dict)


class PipelineTaskResult(SceneOpsBaseModel):
    pipeline_task_id: str
    pipeline_task_run_id: str | None = None

    job_type: JobType | str | None = None
    job_id: str | None = None
    job_status: JobStatus | str | None = None

    refs: JsonDict = Field(default_factory=dict)
    summary: JsonDict = Field(default_factory=dict)
    raw_result: JsonDict = Field(default_factory=dict)

    error: ErrorInfo | None = None


class PipelineRunResult(SceneOpsBaseModel):
    summary: JsonDict = Field(default_factory=dict)
    lineage: PipelineLineage = Field(default_factory=PipelineLineage)
    outputs: dict[str, JsonDict] = Field(default_factory=dict)

    tasks: list[PipelineTaskResult] = Field(default_factory=list)
