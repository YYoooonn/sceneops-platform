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
        if params.evaluator_id == "center-distance":
            return self._run_center_distance(params=params, job=job)

        raise ValueError(f"Unsupported evaluator: {params.evaluator_id}")

    def _run_center_distance(
        self, *, params: EvaluateDetectionJobParams, job: JobManifest
    ) -> EvaluateDetectionJobResult:
        evaluation_run_id = params.evaluation_run_id or default_evaluation_run_id(
            job.job_id
        )

        evaluation_manifest = evaluate_detection_run(
            manifest_root=self.context.manifest_root,
            runs_root=self.context.runs_root,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            inference_run_id=params.inference_run_id,
            evaluation_run_id=evaluation_run_id,
            match_distance_m=params.match_distance_m,
        )

        evaluation_manifest_uri = evaluation_manifest.get(
            "evaluation_manifest_uri"
        ) or str(
            self.context.runs_root
            / "evaluations"
            / evaluation_run_id
            / "evaluation.json"
        )

        return EvaluateDetectionJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            inference_run_id=params.inference_run_id,
            evaluation_run_id=evaluation_run_id,
            evaluation_manifest_uri=evaluation_manifest_uri,
            metrics=evaluation_manifest.get("metrics", {}),
            sample_count=evaluation_manifest.get("sample_count"),
            result_summary={
                "status": evaluation_manifest.get("status"),
                "match_distance_m": evaluation_manifest.get("match_distance_m"),
                "class_metrics": evaluation_manifest.get("class_metrics", {}),
                "samples_root_uri": evaluation_manifest.get("samples_root_uri"),
                "created_at": evaluation_manifest.get("created_at"),
            },
        )
