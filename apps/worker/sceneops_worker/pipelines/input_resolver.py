"""PipelineInputResolver — resolves task inputs into a PipelineTaskInputs envelope.

Each pipeline task resolves its own inputs from pipeline_run_id + task_id using
DB records.  No in-memory context propagation is used.

Resolution:
  pipeline  — from pipeline_run / task_definition / task_run (always authoritative)
  dataset   — DatasetInputRef built from DatasetVersionRecord (baseline quality cache)
  model     — ModelInputRef built from ModelVersionRecord
  upstream  — PipelineUpstreamTaskRef per upstream task; refs/summary merged into
               inputs.refs / inputs.summary (override dataset baseline in to_context_values)
"""

from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_core.pipelines.schemas import (
    DatasetInputRef,
    ModelInputRef,
    PipelineInputRef,
    PipelineRunManifest,
    PipelineTaskDefinition,
    PipelineTaskInputs,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
    PipelineUpstreamTaskRef,
)
from sceneops_worker.core.context import WorkerContext


class PipelineInputResolver:
    """Resolves inputs for a pipeline task prior to job planning.

    Returns a PipelineTaskInputs envelope.  Call inputs.to_context_values() to
    get the flat dict that job handlers expect via build_step_params(base, context_values).
    """

    def __init__(self, context: WorkerContext) -> None:
        self._context = context

    async def resolve(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_definition: PipelineTaskDefinition,
        task_run: PipelineTaskRunManifest,
    ) -> PipelineTaskInputs:
        """Return PipelineTaskInputs for the given task."""
        dataset = await self._build_dataset_ref(pipeline_run)
        model = await self._build_model_ref(pipeline_run)
        (
            upstream_tasks,
            upstream_refs,
            upstream_summary,
        ) = await self._resolve_upstream_task_refs(
            pipeline_run=pipeline_run,
            task_definition=task_definition,
        )

        return PipelineTaskInputs(
            pipeline=PipelineInputRef(
                pipeline_run_id=pipeline_run.pipeline_run_id,
                pipeline_type=pipeline_run.type,
                task_id=task_definition.pipeline_task_id,
                pipeline_task_id=task_definition.pipeline_task_id,
                pipeline_task_run_id=task_run.pipeline_task_run_id,
            ),
            dataset=dataset,
            model=model,
            upstream_tasks=upstream_tasks,
            # Upstream task results override dataset/model baselines in to_context_values()
            refs=upstream_refs,
            summary=upstream_summary,
        )

    async def _build_dataset_ref(
        self,
        pipeline_run: PipelineRunManifest,
    ) -> DatasetInputRef | None:
        if not pipeline_run.dataset_id:
            return None

        if not pipeline_run.dataset_version:
            return DatasetInputRef(dataset_id=pipeline_run.dataset_id)

        version = await self._context.dataset_store.get_version(
            dataset_id=pipeline_run.dataset_id,
            version=pipeline_run.dataset_version,
        )

        if version is None:
            return DatasetInputRef(
                dataset_id=pipeline_run.dataset_id,
                dataset_version=pipeline_run.dataset_version,
            )

        refs: JsonDict = {}
        if version.validation_report_uri:
            refs["validation_report_uri"] = version.validation_report_uri
        if version.profile_report_uri:
            refs["profile_report_uri"] = version.profile_report_uri

        summary: JsonDict = {}
        if version.scene_count:
            summary["scene_count"] = version.scene_count
        if version.sample_count:
            summary["sample_count"] = version.sample_count
        if version.frame_count:
            summary["frame_count"] = version.frame_count
        if version.channels:
            summary["channels"] = version.channels
        if version.latest_validation_run_id:
            summary["validation_run_id"] = version.latest_validation_run_id
        if version.validation_status is not None:
            summary["validation_status"] = str(version.validation_status)
        if version.should_block_pipeline is not None:
            summary["should_block_pipeline"] = version.should_block_pipeline
        if version.latest_profile_run_id:
            summary["profile_run_id"] = version.latest_profile_run_id

        return DatasetInputRef(
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            manifest_uri=version.manifest_uri or None,
            required_channels=version.required_channels,
            refs=refs,
            summary=summary,
        )

    async def _build_model_ref(
        self,
        pipeline_run: PipelineRunManifest,
    ) -> ModelInputRef | None:
        if not pipeline_run.model_id:
            return None

        if not pipeline_run.model_version:
            return ModelInputRef(model_id=pipeline_run.model_id)

        version = await self._context.model_store.get_version(
            model_id=pipeline_run.model_id,
            version=pipeline_run.model_version,
        )

        if version is None:
            return ModelInputRef(
                model_id=pipeline_run.model_id,
                model_version=pipeline_run.model_version,
            )

        refs: JsonDict = {}
        if version.endpoint_url:
            refs["model_endpoint_url"] = version.endpoint_url
        if version.artifact_manifest_uri:
            refs["model_artifact_manifest_uri"] = version.artifact_manifest_uri

        return ModelInputRef(
            model_id=pipeline_run.model_id,
            model_version=pipeline_run.model_version,
            model_uri=version.model_uri or None,
            backend=version.backend.value,
            refs=refs,
            runtime=version.runtime or {},
        )

    async def _resolve_upstream_task_refs(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        task_definition: PipelineTaskDefinition,
    ) -> tuple[dict[str, PipelineUpstreamTaskRef], JsonDict, JsonDict]:
        """Load SUCCEEDED upstream task runs from DB and extract normalized outputs.

        Returns:
          - upstream_tasks: PipelineUpstreamTaskRef per upstream task_id
          - upstream_refs: merged refs from all SUCCEEDED upstream tasks
          - upstream_summary: merged summary from all SUCCEEDED upstream tasks

        Merge order across upstream tasks: later tasks in depends_on list win.
        raw_result is retained in PipelineUpstreamTaskRef for debugging but is
        not merged into the returned refs/summary.
        """
        if not task_definition.depends_on_pipeline_task_ids:
            return {}, {}, {}

        upstream_tasks: dict[str, PipelineUpstreamTaskRef] = {}
        upstream_refs: JsonDict = {}
        upstream_summary: JsonDict = {}

        for upstream_task_id in task_definition.depends_on_pipeline_task_ids:
            upstream_run = await self._context.pipeline_store.find_task(
                pipeline_run_id=pipeline_run.pipeline_run_id,
                task_id=upstream_task_id,
            )

            if upstream_run is None:
                upstream_tasks[upstream_task_id] = PipelineUpstreamTaskRef(
                    pipeline_task_id=upstream_task_id,
                    status="missing",
                )
                continue

            if upstream_run.status != PipelineTaskRunStatus.SUCCEEDED:
                upstream_tasks[upstream_task_id] = PipelineUpstreamTaskRef(
                    pipeline_task_id=upstream_task_id,
                    pipeline_task_run_id=upstream_run.pipeline_task_run_id,
                    job_id=upstream_run.job_id,
                    status=upstream_run.status,
                )
                continue

            refs_dict: JsonDict = {}
            summary_dict: JsonDict = {}
            raw_dict: JsonDict = {}

            task_result = upstream_run.result
            if task_result is not None:
                refs_dict = task_result.refs
                summary_dict = task_result.summary
                raw_dict = task_result.raw_result

            upstream_tasks[upstream_task_id] = PipelineUpstreamTaskRef(
                pipeline_task_id=upstream_task_id,
                pipeline_task_run_id=upstream_run.pipeline_task_run_id,
                job_id=upstream_run.job_id,
                status=upstream_run.status,
                refs=refs_dict,
                summary=summary_dict,
                raw_result=raw_dict,
            )

            # Merge refs and summary into top-level inputs.refs / inputs.summary.
            # These override dataset/model baselines in PipelineTaskInputs.to_context_values().
            for k, v in refs_dict.items():
                if v is not None:
                    upstream_refs[k] = v
            for k, v in summary_dict.items():
                if v is not None:
                    upstream_summary[k] = v

        return upstream_tasks, upstream_refs, upstream_summary
