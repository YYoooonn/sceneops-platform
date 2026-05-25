from __future__ import annotations

from sceneops_core.ids.runs import default_evaluation_run_id
from sceneops_core.schemas.jobs import (
    EvaluateDetectionJobParams,
    EvaluateDetectionJobResult,
    JobManifest,
    JobType,
)
from sceneops_worker.evaluation.detection import evaluate_detection_run
from sceneops_worker.jobs.handlers.base import TypedJobHandler


class EvaluateDetectionJobHandler(
    TypedJobHandler[EvaluateDetectionJobParams, EvaluateDetectionJobResult]
):
    job_type = JobType.EVALUATE_DETECTION

    def parse_params(self, job: JobManifest) -> EvaluateDetectionJobParams:
        return EvaluateDetectionJobParams.model_validate(job.params)

    def run(
        self,
        *,
        params: EvaluateDetectionJobParams,
        job: JobManifest,
    ) -> EvaluateDetectionJobResult:
        if params.evaluatorId == "center-distance":
            return self._run_center_distance(params=params, job=job)

        raise ValueError(f"Unsupported evaluator: {params.evaluatorId}")

    def _run_center_distance(
        self, *, params: EvaluateDetectionJobParams, job: JobManifest
    ) -> EvaluateDetectionJobResult:
        evaluation_run_id = params.evaluationRunId or default_evaluation_run_id(
            job.jobId
        )

        evaluation_manifest = evaluate_detection_run(
            manifest_root=self.context.manifest_root,
            runs_root=self.context.runs_root,
            dataset_id=params.datasetId,
            dataset_version=params.datasetVersion,
            inference_run_id=params.inferenceRunId,
            evaluation_run_id=evaluation_run_id,
            match_distance_m=params.matchDistanceM,
        )

        evaluation_manifest_uri = evaluation_manifest.get(
            "evaluationManifestUri"
        ) or str(
            self.context.runs_root
            / "evaluations"
            / evaluation_run_id
            / "evaluation.json"
        )

        return EvaluateDetectionJobResult(
            datasetId=params.datasetId,
            datasetVersion=params.datasetVersion,
            inferenceRunId=params.inferenceRunId,
            evaluationRunId=evaluation_run_id,
            evaluationManifestUri=evaluation_manifest_uri,
            metrics=evaluation_manifest.get("metrics", {}),
            sampleCount=evaluation_manifest.get("sampleCount"),
            resultSummary={
                "status": evaluation_manifest.get("status"),
                "matchDistanceM": evaluation_manifest.get("matchDistanceM"),
                "classMetrics": evaluation_manifest.get("classMetrics", {}),
                "samplesRootUri": evaluation_manifest.get("samplesRootUri"),
                "createdAt": evaluation_manifest.get("createdAt"),
            },
        )
