from __future__ import annotations

from sceneops_core.ids.jobs import generate_job_id
from sceneops_core.schemas.common import JsonDict
from sceneops_core.schemas.jobs import (
    JobManifest,
    JobStatus,
    JobType,
    build_default_steps,
)
from sceneops_core.schemas.pipelines import PipelineRunManifest, PipelineStepRunManifest
from sceneops_core.time import utc_now
from sceneops_worker.pipelines.context import PipelineExecutionContext


class PipelineJobPlanner:
    def build_job_for_step(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> JobManifest:
        now = utc_now()
        job_type = JobType(step.job_type)
        params = self.build_step_job_params(
            pipeline_run=pipeline_run,
            step=step,
            context=context,
        )

        return JobManifest(
            job_id=generate_job_id(),
            type=job_type,
            status=JobStatus.PENDING,
            dataset_id=pipeline_run.dataset_id,
            dataset_version=pipeline_run.dataset_version,
            params=params,
            steps=build_default_steps(job_type),
            pipeline_run_id=pipeline_run.pipeline_run_id,
            pipeline_step_run_id=step.pipeline_step_run_id,
            pipeline_step_name=step.step_name,
            retry_count=0,
            max_retries=0,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )

    def build_step_job_params(
        self,
        *,
        pipeline_run: PipelineRunManifest,
        step: PipelineStepRunManifest,
        context: PipelineExecutionContext,
    ) -> JsonDict:
        base: JsonDict = {
            "dataset_id": pipeline_run.dataset_id,
            "dataset_version": pipeline_run.dataset_version,
            **(step.params or {}),
        }

        job_type = JobType(step.job_type)

        if job_type == JobType.INGEST_DATASET:
            return {
                "dataset_type": base.get("dataset_type", "nuscenes"),
                **base,
            }

        if job_type == JobType.VALIDATE_DATASET_MANIFEST:
            return {
                **base,
                "require_target_channels": base.get(
                    "require_target_channels",
                    ["CAM_FRONT", "LIDAR_TOP"],
                ),
                "validate_samples": base.get("validate_samples", True),
            }

        if job_type == JobType.PREDICT_DETECTION:
            model_id = (
                pipeline_run.model_id or base.get("model_id") or "centerpoint-mock"
            )
            model_version = (
                pipeline_run.model_version or base.get("model_version") or "v0"
            )

            return {
                **base,
                "model_id": model_id,
                "model_version": model_version,
            }

        if job_type == JobType.EVALUATE_DETECTION:
            inference_run_id = base.get("inference_run_id") or context.get(
                "inference_run_id"
            )

            if inference_run_id is None:
                raise ValueError("inference_run_id is required for evaluation step")

            return {
                **base,
                "inference_run_id": inference_run_id,
            }

        raise ValueError(f"Unsupported pipeline step job type: {job_type}")
