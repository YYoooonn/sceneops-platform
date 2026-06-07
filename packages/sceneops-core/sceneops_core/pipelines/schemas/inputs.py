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
    """Dataset identity and baseline quality-cache refs from DatasetVersionRecord."""

    dataset_id: str | None = None
    dataset_version: str | None = None
    manifest_uri: str | None = None

    # URI refs from the version record (validation/profile report locations)
    refs: JsonDict = Field(default_factory=dict)
    # Counts, status flags, and run IDs cached on the version record
    summary: JsonDict = Field(default_factory=dict)


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
      dataset        — dataset identity and DatasetVersionRecord baseline
      model          — model identity and version configuration
      upstream_tasks — structured refs from completed upstream tasks
      refs           — merged URIs/IDs from upstream task results (override dataset baseline)
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

    # Merged from upstream task results; override dataset/model baselines in to_context_values()
    refs: JsonDict = Field(default_factory=dict)
    summary: JsonDict = Field(default_factory=dict)
    params: JsonDict = Field(default_factory=dict)
    extra: JsonDict = Field(default_factory=dict)

    # pylint: disable=no-member, too-many-branches
    def to_context_values(self) -> JsonDict:
        """Flatten inputs into a dict for job handler build_step_params compatibility.

        Merge priority (lowest → highest, higher values overwrite lower):
          1. dataset.refs / dataset.summary  — DatasetVersionRecord baseline
          2. model fields / model.refs       — ModelVersionRecord
          3. self.refs / self.summary        — upstream task results (override dataset baseline)
          4. self.params / self.extra        — explicit overrides

        PipelineTaskInputs is structured; this adapter exists only for job handler APIs.
        """
        out: JsonDict = {
            "pipeline_run_id": self.pipeline.pipeline_run_id,
            "pipeline_type": str(self.pipeline.pipeline_type),
            "task_id": self.pipeline.task_id,
            "pipeline_task_id": self.pipeline.pipeline_task_id,
            "pipeline_task_run_id": self.pipeline.pipeline_task_run_id,
        }

        # (1) Dataset baseline
        if self.dataset is not None:
            if self.dataset.dataset_id is not None:
                out["dataset_id"] = self.dataset.dataset_id
            if self.dataset.dataset_version is not None:
                out["dataset_version"] = self.dataset.dataset_version
            if self.dataset.manifest_uri is not None:
                out["dataset_manifest_uri"] = self.dataset.manifest_uri
            for k, v in self.dataset.refs.items():
                if v is not None:
                    out[k] = v
            for k, v in self.dataset.summary.items():
                if v is not None:
                    out[k] = v

        # (2) Model
        if self.model is not None:
            if self.model.model_id is not None:
                out["model_id"] = self.model.model_id
            if self.model.model_version is not None:
                out["model_version"] = self.model.model_version
            if self.model.model_uri is not None:
                out["model_uri"] = self.model.model_uri
            if self.model.backend is not None:
                out["model_backend"] = self.model.backend
            for k, v in self.model.refs.items():
                if v is not None:
                    out[k] = v
            if self.model.runtime:
                out["model_runtime"] = self.model.runtime

        # (3) Upstream task results — override dataset/model baseline
        for k, v in self.refs.items():
            if v is not None:
                out[k] = v
        for k, v in self.summary.items():
            if v is not None:
                out[k] = v

        # (4) Explicit overrides
        out.update({k: v for k, v in self.params.items() if v is not None})
        out.update({k: v for k, v in self.extra.items() if v is not None})

        # Structured upstream task map for handler introspection
        out["upstream_tasks"] = {
            tid: ref.model_dump(mode="python")
            for tid, ref in self.upstream_tasks.items()
        }

        return out
