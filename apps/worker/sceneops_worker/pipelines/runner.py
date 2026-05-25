from __future__ import annotations

from sceneops_core.ids.jobs import generate_job_id
from sceneops_core.schemas.common import ErrorInfo, JsonDict
from sceneops_core.schemas.jobs import (
    EvaluateDetectionJobResult,
    IngestDatasetJobResult,
    JobManifest,
    JobStatus,
    JobType,
    PredictDetectionJobResult,
    build_default_steps,
)
from sceneops_core.schemas.pipelines import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineStepRunManifest,
    PipelineStepRunStatus,
)
from sceneops_core.time import utc_now_iso
from sceneops_worker.jobs.runner import JobRunner
from sceneops_worker.jobs.store import JobStore
from sceneops_worker.pipelines.store import PipelineStore


class PipelineRunner:
    def __init__(
        self,
        *,
        pipeline_store: PipelineStore,
        job_store: JobStore,
        job_runner: JobRunner,
    ) -> None:
        self.pipeline_store = pipeline_store
        self.job_store = job_store
        self.job_runner = job_runner

    async def run(self, pipeline_run_id: str) -> PipelineRunManifest:
        pipeline_run = await self.pipeline_store.get_pipeline_run(pipeline_run_id)

        if pipeline_run is None:
            raise FileNotFoundError(f"Pipeline run not found: {pipeline_run_id}")

        self._validate_runnable(pipeline_run)

        pipeline_run = await self._mark_pipeline_running(pipeline_run)

        pipeline_context: JsonDict = {
            "pipelineRunId": pipeline_run.pipelineRunId,
            "datasetId": pipeline_run.datasetId,
            "datasetVersion": pipeline_run.datasetVersion,
            "modelId": pipeline_run.modelId,
            "modelVersion": pipeline_run.modelVersion,
        }

        try:
            steps = await self.pipeline_store.list_steps(pipeline_run_id)
            steps = sorted(steps, key=lambda step: step.stepOrder)

            for step in steps:
                await self._run_step(
                    pipeline_run=pipeline_run,
                    step=step,
                    pipeline_context=pipeline_context,
                )

            pipeline_run = await self._mark_pipeline_succeeded(
                pipeline_run,
                pipeline_context=pipeline_context,
            )
            return pipeline_run

        except Exception as error:
            pipeline_run = await self._mark_pipeline_failed(
                pipeline_run,
                error=ErrorInfo(
                    type=error.__class__.__name__,
                    message=str(error),
                ),
            )
            raise

    def _validate_runnable(self, pipeline_run: PipelineRunManifest) -> None:
        if pipeline_run.status == PipelineRunStatus.SUCCEEDED:
            raise RuntimeError(
                f"Pipeline run is already succeeded: {pipeline_run.pipelineRunId}"
            )

        if pipeline_run.status == PipelineRunStatus.RUNNING:
            raise RuntimeError(
                f"Pipeline run is already running: {pipeline_run.pipelineRunId}"
            )

        if pipeline_run.status == PipelineRunStatus.CANCELED:
            raise RuntimeError(
                f"Pipeline run is canceled: {pipeline_run.pipelineRunId}"
            )

    async def _run_step(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        pipeline_context: JsonDict,
    ) -> PipelineStepRunManifest:
        self._validate_dependencies_succeeded(
            step=step,
            pipeline_context=pipeline_context,
        )

        step = await self._mark_step_running(step)

        try:
            job = self._build_job_for_step(
                pipeline_run=pipeline_run,
                step=step,
                pipeline_context=pipeline_context,
            )

            created_job = await self.job_store.create_job(job)

            step.jobId = created_job.jobId
            step.updatedAt = utc_now_iso()
            step = await self.pipeline_store.save_step(step)

            finished_job = await self.job_runner.run(created_job.jobId)

            if finished_job.result is not None:
                self._apply_step_result_to_context(
                    step=step,
                    result=finished_job.result,
                    pipeline_context=pipeline_context,
                )

            step.status = PipelineStepRunStatus.SUCCEEDED
            step.result = {
                "jobId": finished_job.jobId,
                "jobStatus": finished_job.status.value,
                "jobResult": finished_job.result,
            }
            step.error = None
            step.finishedAt = utc_now_iso()
            step.updatedAt = step.finishedAt

            saved = await self.pipeline_store.save_step(step)

            pipeline_context.setdefault("steps", {})[step.stepName] = {
                "status": saved.status.value,
                "jobId": saved.jobId,
                "result": saved.result,
            }

            return saved

        except Exception as error:
            step.status = PipelineStepRunStatus.FAILED
            step.error = ErrorInfo(
                type=error.__class__.__name__,
                message=str(error),
            )
            step.finishedAt = utc_now_iso()
            step.updatedAt = step.finishedAt

            await self.pipeline_store.save_step(step)
            raise

    def _validate_dependencies_succeeded(
        self,
        *,
        step: PipelineStepRunManifest,
        pipeline_context: JsonDict,
    ) -> None:
        completed_steps = pipeline_context.get("steps", {})

        for dependency in step.dependsOnStepNames:
            dependency_state = completed_steps.get(dependency)

            if dependency_state is None:
                raise RuntimeError(
                    f"Step dependency has not completed: "
                    f"{step.stepName} depends on {dependency}"
                )

            if dependency_state.get("status") != PipelineStepRunStatus.SUCCEEDED.value:
                raise RuntimeError(
                    f"Step dependency is not succeeded: "
                    f"{step.stepName} depends on {dependency}"
                )

    async def _mark_step_running(
        self,
        step: PipelineStepRunManifest,
    ) -> PipelineStepRunManifest:
        now = utc_now_iso()

        step.status = PipelineStepRunStatus.RUNNING
        step.startedAt = step.startedAt or now
        step.updatedAt = now
        step.error = None

        return await self.pipeline_store.save_step(step)

    def _build_job_for_step(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        pipeline_context: JsonDict,
    ) -> JobManifest:
        now = utc_now_iso()
        job_type = JobType(step.jobType)

        params = self._build_step_job_params(
            pipeline_run=pipeline_run,
            step=step,
            pipeline_context=pipeline_context,
        )

        return JobManifest(
            jobId=generate_job_id(),
            type=job_type,
            status=JobStatus.PENDING,
            datasetId=pipeline_run.datasetId,
            datasetVersion=pipeline_run.datasetVersion,
            params=params,
            steps=build_default_steps(job_type),
            pipelineRunId=pipeline_run.pipelineRunId,
            pipelineStepRunId=step.pipelineStepRunId,
            pipelineStepName=step.stepName,
            retryCount=0,
            maxRetries=0,
            queuedAt=now,
            createdAt=now,
            updatedAt=now,
        )

    def _build_step_job_params(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        pipeline_context: JsonDict,
    ) -> JsonDict:
        base: JsonDict = {
            "datasetId": pipeline_run.datasetId,
            "datasetVersion": pipeline_run.datasetVersion,
            **step.params,
        }

        job_type = JobType(step.jobType)

        if job_type == JobType.INGEST_DATASET:
            return {
                "datasetType": base.get("datasetType", "nuscenes"),
                **base,
            }

        if job_type == JobType.PREDICT_DETECTION:
            model_id = pipeline_run.modelId or base.get("modelId") or "centerpoint-mock"
            model_version = (
                pipeline_run.modelVersion or base.get("modelVersion") or "v0"
            )

            # inferenceRunId는 여기서 생성하지 않는다.
            # 명시적으로 들어온 경우에만 유지하고, 없으면 handler가 jobId 기준으로 생성한다.
            return {
                **base,
                "modelId": model_id,
                "modelVersion": model_version,
            }

        if job_type == JobType.EVALUATE_DETECTION:
            inference_run_id = base.get("inferenceRunId") or pipeline_context.get(
                "inferenceRunId"
            )

            if inference_run_id is None:
                raise ValueError("inferenceRunId is required for evaluation step")

            # evaluationRunId도 여기서 강제로 만들지 않는다.
            # 명시적으로 들어온 경우에만 유지하고, 없으면 handler가 jobId 기준으로 생성한다.
            return {
                **base,
                "inferenceRunId": inference_run_id,
            }

        raise ValueError(f"Unsupported pipeline step job type: {job_type}")

    def _apply_step_result_to_context(
        self,
        *,
        step: PipelineStepRunManifest,
        result: JsonDict,
        pipeline_context: JsonDict,
    ) -> None:
        job_type = JobType(step.jobType)

        if job_type == JobType.INGEST_DATASET:
            parsed = IngestDatasetJobResult.model_validate(result)

            pipeline_context["datasetManifestUri"] = parsed.manifestUri
            pipeline_context["sceneCount"] = parsed.sceneCount
            pipeline_context["sampleCount"] = parsed.sampleCount
            return

        if job_type == JobType.PREDICT_DETECTION:
            parsed = PredictDetectionJobResult.model_validate(result)

            pipeline_context["inferenceRunId"] = parsed.inferenceRunId
            pipeline_context["predictionManifestUri"] = parsed.predictionManifestUri
            pipeline_context["predictionSampleCount"] = parsed.sampleCount
            return

        if job_type == JobType.EVALUATE_DETECTION:
            parsed = EvaluateDetectionJobResult.model_validate(result)

            pipeline_context["evaluationRunId"] = parsed.evaluationRunId
            pipeline_context["evaluationManifestUri"] = parsed.evaluationManifestUri
            pipeline_context["metrics"] = parsed.metrics
            pipeline_context["evaluationSampleCount"] = parsed.sampleCount
            return

    async def _mark_pipeline_running(
        self,
        pipeline_run: PipelineRunManifest,
    ) -> PipelineRunManifest:
        now = utc_now_iso()

        pipeline_run.status = PipelineRunStatus.RUNNING
        pipeline_run.startedAt = pipeline_run.startedAt or now
        pipeline_run.updatedAt = now
        pipeline_run.finishedAt = None
        pipeline_run.error = None

        return await self.pipeline_store.save_pipeline_run(pipeline_run)

    async def _mark_pipeline_succeeded(
        self,
        pipeline_run: PipelineRunManifest,
        *,
        pipeline_context: JsonDict,
    ) -> PipelineRunManifest:
        now = utc_now_iso()

        steps = await self.pipeline_store.list_steps(pipeline_run.pipelineRunId)

        pipeline_run.status = PipelineRunStatus.SUCCEEDED
        pipeline_run.result = {
            "datasetManifestUri": pipeline_context.get("datasetManifestUri"),
            "inferenceRunId": pipeline_context.get("inferenceRunId"),
            "predictionManifestUri": pipeline_context.get("predictionManifestUri"),
            "evaluationRunId": pipeline_context.get("evaluationRunId"),
            "evaluationManifestUri": pipeline_context.get("evaluationManifestUri"),
            "metrics": pipeline_context.get("metrics"),
            "steps": [
                {
                    "stepName": step.stepName,
                    "status": step.status.value
                    if hasattr(step.status, "value")
                    else str(step.status),
                    "jobId": step.jobId,
                    "result": step.result,
                }
                for step in sorted(steps, key=lambda item: item.stepOrder)
            ],
        }
        pipeline_run.error = None
        pipeline_run.finishedAt = now
        pipeline_run.updatedAt = now

        return await self.pipeline_store.save_pipeline_run(pipeline_run)

    async def _mark_pipeline_failed(
        self,
        pipeline_run: PipelineRunManifest,
        *,
        error: ErrorInfo,
    ) -> PipelineRunManifest:
        now = utc_now_iso()

        pipeline_run.status = PipelineRunStatus.FAILED
        pipeline_run.error = error
        pipeline_run.finishedAt = now
        pipeline_run.updatedAt = now

        return await self.pipeline_store.save_pipeline_run(pipeline_run)
