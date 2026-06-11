from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import default_evaluation_run_id, generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.evaluations.schemas import EvaluationTaskType
from sceneops_core.evaluations.schemas.manifests import DetectionEvaluationManifest
from sceneops_core.evaluations.schemas.runs import EvaluationRunRecord
from sceneops_core.inference.schemas.runs import InferenceRunRecord
from sceneops_core.jobs.schemas import (
    EvaluateDetectionJobParams,
    EvaluateDetectionJobResult,
    JobManifest,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.evaluation import create_detection_evaluator
from sceneops_worker.evaluation.detection import DetectionEvaluationRequest
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler


@dataclass(frozen=True)
class EvaluateDetectionExecution:
    """Resolved execution context for one evaluate_detection job invocation."""

    job: JobManifest
    params: EvaluateDetectionJobParams
    context: WorkerContext
    evaluation_run_id: str
    dataset_version_record: DatasetVersionRecord
    inference_run_record: InferenceRunRecord


@dataclass(frozen=True)
class EvaluateDetectionInputs:
    """Resolved dataset manifest."""

    dataset_manifest_uri: str
    dataset_manifest: Any  # DatasetManifest — typed at use site


@dataclass(frozen=True)
class EvaluateDetectionArtifacts:
    """Registered artifact URIs for one evaluation run."""

    evaluation_manifest_uri: str
    metrics_uri: str


@dataclass(frozen=True)
class EvaluationCounts:
    """Aggregated counts extracted from a DetectionEvaluationManifest."""

    sample_count: int
    prediction_count: int
    evaluable_prediction_count: int
    lifting_failed_prediction_count: int
    ground_truth_count: int
    evaluation_unit: str
    primary_metric_name: str | None
    primary_metric_value: float | None


class EvaluateDetectionJobHandler(
    RunRecordHandler[
        EvaluateDetectionJobParams, EvaluateDetectionJobResult, EvaluationRunRecord
    ],
    JobHandler[EvaluateDetectionJobParams, EvaluateDetectionJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.EVALUATE_DETECTION

    @property
    def params_model(self) -> type[EvaluateDetectionJobParams]:
        return EvaluateDetectionJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        inference_run_id = inputs.refs.get("inference_run_id")
        if inference_run_id is None:
            raise ValueError("inference_run_id is required for evaluate_detection")
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "inference_run_id": inference_run_id,
        }

    def build_initial_record(
        self,
        *,
        job: Any,
        params: EvaluateDetectionJobParams,
        started_at: datetime,
    ) -> EvaluationRunRecord:
        evaluation_run_id = params.evaluation_run_id or default_evaluation_run_id(
            job.job_id
        )
        return EvaluationRunRecord(
            run_id=evaluation_run_id,
            inference_run_id=params.inference_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            task_type=EvaluationTaskType.DETECTION,
            evaluator_id=params.evaluator_id,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_task_run_id=job.pipeline_task_run_id,
            job_id=job.job_id,
            metadata={"match_distance_m": params.match_distance_m},
            started_at=started_at,
        )

    # ── orchestration ──────────────────────────────────────────────────────────

    async def execute(
        self,
        *,
        job: Any,
        params: EvaluateDetectionJobParams,
        context: WorkerContext,
        initial_record: EvaluationRunRecord,
        started_at: datetime,
    ) -> tuple[EvaluationRunRecord, EvaluateDetectionJobResult]:
        execution = await self._prepare_execution(
            job=job, params=params, context=context, initial_record=initial_record
        )
        inputs = await self._resolve_inputs(execution)

        await self._patch_initial_record_with_model_identity(
            execution=execution,
            initial_record=initial_record,
        )

        evaluation_manifest = await self._run_evaluation(
            execution=execution,
            inputs=inputs,
        )

        counts = self._extract_evaluation_counts(evaluation_manifest)

        artifacts = await self._write_and_register_artifacts(
            execution=execution,
            evaluation_manifest=evaluation_manifest,
            counts=counts,
        )

        succeeded_record = self._build_succeeded_record(
            initial_record=initial_record,
            execution=execution,
            evaluation_manifest=evaluation_manifest,
            artifacts=artifacts,
            counts=counts,
        )

        job_result = self._build_result(
            execution=execution,
            evaluation_manifest=evaluation_manifest,
            artifacts=artifacts,
            counts=counts,
        )

        return succeeded_record, job_result

    # ── execution resolution ───────────────────────────────────────────────────

    async def _prepare_execution(
        self,
        *,
        job: Any,
        params: EvaluateDetectionJobParams,
        context: WorkerContext,
        initial_record: EvaluationRunRecord,
    ) -> EvaluateDetectionExecution:
        dataset_version = await self._require_ready_dataset_version(
            context, params.dataset_id, params.dataset_version
        )
        inference_run = await self._require_inference_run(
            context, params.inference_run_id
        )
        self._validate_inference_run_matches_dataset(
            inference_run=inference_run,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )
        return EvaluateDetectionExecution(
            job=job,
            params=params,
            context=context,
            evaluation_run_id=initial_record.run_id,
            dataset_version_record=dataset_version,
            inference_run_record=inference_run,
        )

    @staticmethod
    async def _require_ready_dataset_version(
        context: WorkerContext,
        dataset_id: str,
        dataset_version: str,
    ) -> DatasetVersionRecord:
        version = await context.dataset_store.get_version(
            dataset_id=dataset_id,
            version=dataset_version,
        )
        if version is None:
            raise ValueError(
                f"Dataset version not found: {dataset_id}:{dataset_version}"
            )
        if version.status != DatasetVersionStatus.READY:
            raise ValueError(
                f"Dataset version is not usable for evaluation: "
                f"{dataset_id}:{dataset_version}, status={version.status}"
            )
        if version.manifest_uri is None:
            raise ValueError(
                f"Dataset version has no manifest_uri: {dataset_id}:{dataset_version}"
            )
        return version

    @staticmethod
    async def _require_inference_run(
        context: WorkerContext,
        inference_run_id: str,
    ) -> InferenceRunRecord:
        inference_run = await context.runs.inference.get(inference_run_id)
        if inference_run is None:
            raise ValueError(f"Inference run not found: {inference_run_id}")
        if inference_run.status != RunStatus.SUCCEEDED:
            raise ValueError(
                f"Inference run is not complete: "
                f"run_id={inference_run_id}, status={inference_run.status}"
            )
        if inference_run.prediction_manifest_uri is None:
            raise ValueError(
                f"Inference run has no prediction_manifest_uri: {inference_run_id}"
            )
        return inference_run

    @staticmethod
    def _validate_inference_run_matches_dataset(
        *,
        inference_run: InferenceRunRecord,
        dataset_id: str,
        dataset_version: str,
    ) -> None:
        if (
            inference_run.dataset_id != dataset_id
            or inference_run.dataset_version != dataset_version
        ):
            raise ValueError(
                f"Inference run dataset mismatch: "
                f"run {inference_run.run_id!r} is for "
                f"{inference_run.dataset_id}/{inference_run.dataset_version}, "
                f"but evaluation params request {dataset_id}/{dataset_version}"
            )

    # ── input resolution ───────────────────────────────────────────────────────

    @staticmethod
    async def _resolve_inputs(
        execution: EvaluateDetectionExecution,
    ) -> EvaluateDetectionInputs:
        version = execution.dataset_version_record
        dataset_manifest = (
            await execution.context.dataset_artifact_store.load_dataset_manifest(
                version.manifest_uri
            )
        )
        return EvaluateDetectionInputs(
            dataset_manifest=dataset_manifest,
            dataset_manifest_uri=version.manifest_uri,
        )

    # ── record patch ───────────────────────────────────────────────────────────

    async def _patch_initial_record_with_model_identity(
        self,
        *,
        execution: EvaluateDetectionExecution,
        initial_record: EvaluationRunRecord,
    ) -> None:
        """Persist model_id/model_version onto the initial record.

        These are not available at record creation time; we get them from the
        inference run, which is resolved in _prepare_execution.
        """
        inference_run = execution.inference_run_record
        await self._upsert(
            execution.context,
            initial_record.model_copy(
                update={
                    "model_id": inference_run.model_id,
                    "model_version": inference_run.model_version,
                }
            ),
        )

    # ── evaluation ────────────────────────────────────────────────────────────

    @staticmethod
    async def _run_evaluation(
        *,
        execution: EvaluateDetectionExecution,
        inputs: EvaluateDetectionInputs,
    ) -> DetectionEvaluationManifest:
        params = execution.params
        context = execution.context
        evaluator = create_detection_evaluator(params.evaluator_id)
        return await evaluator.run(
            DetectionEvaluationRequest(
                dataset_manifest=inputs.dataset_manifest,
                scene_artifact_store=context.scene_artifact_store,
                run_artifact_store=context.run_artifact_store,
                inference_run_id=params.inference_run_id,
                evaluation_run_id=execution.evaluation_run_id,
                match_distance_m=params.match_distance_m,
                missing_gt_policy=params.missing_gt_policy,
            )
        )

    # ── artifact writing / registration ───────────────────────────────────────

    @staticmethod
    async def _write_metrics_artifact(
        *,
        execution: EvaluateDetectionExecution,
        evaluation_manifest: DetectionEvaluationManifest,
        counts: EvaluationCounts,
    ) -> str:
        params = execution.params
        inference_run = execution.inference_run_record
        return await execution.context.run_artifact_store.write_evaluation_run_metrics(
            evaluation_run_id=execution.evaluation_run_id,
            metrics={
                "evaluation_run_id": execution.evaluation_run_id,
                "inference_run_id": params.inference_run_id,
                "dataset_id": params.dataset_id,
                "dataset_version": params.dataset_version,
                "model_id": inference_run.model_id,
                "model_version": inference_run.model_version,
                "evaluator_id": params.evaluator_id,
                "match_distance_m": params.match_distance_m,
                "primary_metric_name": counts.primary_metric_name,
                "primary_metric_value": counts.primary_metric_value,
                "metrics": evaluation_manifest.metrics,
                "class_metrics": evaluation_manifest.class_metrics,
                "sample_count": counts.sample_count,
                "prediction_count": counts.prediction_count,
                "evaluable_prediction_count": counts.evaluable_prediction_count,
                "lifting_failed_prediction_count": counts.lifting_failed_prediction_count,
                "ground_truth_count": counts.ground_truth_count,
                "evaluation_unit": counts.evaluation_unit,
            },
        )

    @staticmethod
    async def _register_artifacts(
        *,
        execution: EvaluateDetectionExecution,
        evaluation_manifest_uri: str,
        metrics_uri: str,
    ) -> EvaluateDetectionArtifacts:
        context = execution.context
        job = execution.job
        params = execution.params
        evaluation_run_id = execution.evaluation_run_id

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.EVALUATION_MANIFEST,
                uri=evaluation_manifest_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.EVALUATION_RUN,
            owner_id=evaluation_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=evaluation_run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        await context.artifact_record_store.create(
            artifact_id=generate_artifact_id(),
            ref=ArtifactRef(
                kind=ArtifactKind.METRICS,
                uri=metrics_uri,
                media_type="application/json",
            ),
            owner_type=ArtifactOwnerType.EVALUATION_RUN,
            owner_id=evaluation_run_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=evaluation_run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        return EvaluateDetectionArtifacts(
            evaluation_manifest_uri=evaluation_manifest_uri,
            metrics_uri=metrics_uri,
        )

    async def _write_and_register_artifacts(
        self,
        *,
        execution: EvaluateDetectionExecution,
        evaluation_manifest: DetectionEvaluationManifest,
        counts: EvaluationCounts,
    ) -> EvaluateDetectionArtifacts:
        metrics_uri = await self._write_metrics_artifact(
            execution=execution,
            evaluation_manifest=evaluation_manifest,
            counts=counts,
        )

        evaluation_manifest_uri = evaluation_manifest.evaluation_manifest_uri
        if not evaluation_manifest_uri:
            raise ValueError(
                f"evaluate_detection succeeded without evaluation_manifest_uri "
                f"(evaluation_run_id={execution.evaluation_run_id})"
            )
        if not metrics_uri:
            raise ValueError(
                f"evaluate_detection succeeded without metrics_uri "
                f"(evaluation_run_id={execution.evaluation_run_id})"
            )

        return await self._register_artifacts(
            execution=execution,
            evaluation_manifest_uri=evaluation_manifest_uri,
            metrics_uri=metrics_uri,
        )

    # ── result assembly ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_evaluation_counts(
        evaluation_manifest: DetectionEvaluationManifest,
    ) -> EvaluationCounts:
        return EvaluationCounts(
            sample_count=evaluation_manifest.sample_count or 0,
            prediction_count=evaluation_manifest.prediction_count or 0,
            evaluable_prediction_count=evaluation_manifest.evaluable_prediction_count
            or 0,
            lifting_failed_prediction_count=evaluation_manifest.lifting_failed_prediction_count
            or 0,
            ground_truth_count=evaluation_manifest.ground_truth_count or 0,
            evaluation_unit=evaluation_manifest.evaluation_unit or "annotation",
            primary_metric_name=evaluation_manifest.primary_metric_name,
            primary_metric_value=evaluation_manifest.primary_metric_value,
        )

    def _build_succeeded_record(
        self,
        *,
        initial_record: EvaluationRunRecord,
        execution: EvaluateDetectionExecution,
        evaluation_manifest: DetectionEvaluationManifest,
        artifacts: EvaluateDetectionArtifacts,
        counts: EvaluationCounts,
    ) -> EvaluationRunRecord:
        inference_run = execution.inference_run_record
        return initial_record.model_copy(
            update={
                "model_id": inference_run.model_id,
                "model_version": inference_run.model_version,
                "status": RunStatus.SUCCEEDED,
                "sample_count": counts.sample_count,
                "prediction_count": counts.prediction_count,
                "ground_truth_count": counts.ground_truth_count,
                "evaluation_unit": counts.evaluation_unit,
                "primary_metric_name": counts.primary_metric_name,
                "primary_metric_value": counts.primary_metric_value,
                "evaluation_manifest_uri": artifacts.evaluation_manifest_uri,
                "metrics_uri": artifacts.metrics_uri,
                "metrics": evaluation_manifest.metrics,
                "class_metrics": evaluation_manifest.class_metrics,
                "summary": _build_evaluation_summary(
                    evaluation_manifest=evaluation_manifest,
                    counts=counts,
                ),
                "finished_at": utc_now(),
            }
        )

    @staticmethod
    def _build_result(
        *,
        execution: EvaluateDetectionExecution,
        evaluation_manifest: DetectionEvaluationManifest,
        artifacts: EvaluateDetectionArtifacts,
        counts: EvaluationCounts,
    ) -> EvaluateDetectionJobResult:
        params = execution.params
        inference_run = execution.inference_run_record
        return EvaluateDetectionJobResult(
            evaluation_run_id=execution.evaluation_run_id,
            evaluation_manifest_uri=artifacts.evaluation_manifest_uri,
            metrics_uri=artifacts.metrics_uri,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=inference_run.model_id,
            model_version=inference_run.model_version,
            inference_run_id=params.inference_run_id,
            sample_count=counts.sample_count,
            prediction_count=counts.prediction_count,
            evaluable_prediction_count=counts.evaluable_prediction_count,
            lifting_failed_prediction_count=counts.lifting_failed_prediction_count,
            ground_truth_count=counts.ground_truth_count,
            evaluation_unit=counts.evaluation_unit,
            primary_metric_name=counts.primary_metric_name,
            primary_metric_value=counts.primary_metric_value,
            metrics=evaluation_manifest.metrics,
            class_metrics=evaluation_manifest.class_metrics,
            summary=_build_evaluation_summary(
                evaluation_manifest=evaluation_manifest,
                counts=counts,
            ),
            metadata={
                "evaluator_id": params.evaluator_id,
                "match_distance_m": params.match_distance_m,
                "missing_gt_policy": params.missing_gt_policy,
            },
        )

    # ── run record lifecycle hook ─────────────────────────────────────────────

    async def _upsert(
        self, context: WorkerContext, record: EvaluationRunRecord
    ) -> EvaluationRunRecord:
        return await context.runs.evaluations.upsert(record)


# ── module-level helpers ──────────────────────────────────────────────────────


def _build_evaluation_summary(
    *,
    evaluation_manifest: DetectionEvaluationManifest,
    counts: EvaluationCounts,
) -> JsonDict:
    metadata = evaluation_manifest.metadata or {}

    evaluated_scene_ids = metadata.get("evaluated_scene_ids", [])
    skipped_scene_ids = metadata.get("skipped_scene_ids", [])
    is_skipped = evaluation_manifest.status == "skipped"

    return {
        "status": evaluation_manifest.status,
        "skipped": is_skipped,
        "warning": metadata.get("reason") if is_skipped else None,
        "match_distance_m": evaluation_manifest.match_distance_m,
        "samples_root_uri": evaluation_manifest.samples_root_uri,
        "prediction_count": counts.prediction_count,
        "evaluable_prediction_count": counts.evaluable_prediction_count,
        "lifting_failed_prediction_count": counts.lifting_failed_prediction_count,
        "ground_truth_count": counts.ground_truth_count,
        "evaluation_unit": counts.evaluation_unit,
        "primary_metric_name": counts.primary_metric_name,
        "primary_metric_value": counts.primary_metric_value,
        "evaluated_scene_ids": evaluated_scene_ids[:50],
        "evaluated_scene_count": len(evaluated_scene_ids),
        "skipped_scene_ids": skipped_scene_ids[:50],
        "skipped_scene_count": len(skipped_scene_ids),
    }
