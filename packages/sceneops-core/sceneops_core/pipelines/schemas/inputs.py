from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel

from .enums import PipelineTaskRunStatus, PipelineType


class PipelineInputRef(SceneOpsBaseModel):
    """Stable pipeline-level identity for a single task invocation."""

    pipeline_run_id: str
    pipeline_type: PipelineType | str
    task_id: str
    pipeline_task_id: str
    pipeline_task_run_id: str


class DatasetInputRef(SceneOpsBaseModel):
    """The DatasetVersion scope a pipeline run executes in. Scope only: no
    channel requirements, counts or quality state are carried here."""

    dataset_id: str | None = None
    dataset_version: str | None = None


class ModelInputRef(SceneOpsBaseModel):
    """Model identity, artifact URI, and runtime configuration."""

    model_id: str | None = None
    model_version: str | None = None
    model_uri: str | None = None
    backend: str | None = None

    # URI refs (endpoint_url, artifact_manifest_uri)
    refs: JsonDict = Field(default_factory=dict)
    # Backend-specific runtime configuration
    runtime: JsonDict = Field(default_factory=dict)


class PipelineUpstreamTaskRef(SceneOpsBaseModel):
    """Reference to a completed upstream task and its normalized outputs."""

    pipeline_task_id: str
    pipeline_task_run_id: str | None = None
    job_id: str | None = None
    status: PipelineTaskRunStatus | str | None = None
    refs: JsonDict = Field(default_factory=dict)
    summary: JsonDict = Field(default_factory=dict)
    raw_result: JsonDict = Field(default_factory=dict)


class PipelineTaskInputs(SceneOpsBaseModel):
    """Compact typed envelope carrying all inputs for one pipeline task execution.

    Structure:
      pipeline       — stable task-level identity
      dataset        — the DatasetVersion scope
      model          — model identity and version configuration
      upstream_tasks — structured refs from completed upstream tasks
      refs           — merged URIs/IDs from upstream task results
      summary        — merged status/count summaries from upstream task results
      params         — explicit task-level params (e.g. from PipelineTaskDefinition.default_params)
      extra          — caller-supplied overrides

    New task-specific values should go into refs/summary/extra rather than
    becoming new top-level fields on this class.
    """

    pipeline: PipelineInputRef
    dataset: DatasetInputRef | None = None
    model: ModelInputRef | None = None

    upstream_tasks: dict[str, PipelineUpstreamTaskRef] = Field(default_factory=dict)

    # Merged from upstream task results
    refs: JsonDict = Field(default_factory=dict)
    summary: JsonDict = Field(default_factory=dict)
    params: JsonDict = Field(default_factory=dict)
    extra: JsonDict = Field(default_factory=dict)
