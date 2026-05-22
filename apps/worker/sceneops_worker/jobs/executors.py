from pathlib import Path
from typing import Any

from sceneops_core.ids.runs import (
    default_evaluation_run_id,
    default_inference_run_id,
)
from sceneops_core.schemas.jobs import JobManifest, JobType

from sceneops_worker.evaluation.detection import evaluate_detection_run
from sceneops_worker.ingest.nuscenes import IngestMode, ingest_nuscenes
from sceneops_worker.predictions.mock_detection import generate_mock_predictions


class JobExecutionContext:
    def __init__(
        self,
        *,
        raw_data_root: Path,
        manifest_root: Path,
        artifact_root: Path,
        runs_root: Path,
        default_dataset_id: str,
        default_dataset_version: str,
    ) -> None:
        self.raw_data_root = raw_data_root
        self.manifest_root = manifest_root
        self.artifact_root = artifact_root
        self.runs_root = runs_root
        self.default_dataset_id = default_dataset_id
        self.default_dataset_version = default_dataset_version


class JobExecutor:
    def __init__(self, context: JobExecutionContext) -> None:
        self.context = context

    def execute(self, job: JobManifest) -> dict[str, Any]:
        if job.type == JobType.INGEST_NUSCENES:
            return self._execute_ingest_nuscenes(job)

        if job.type == JobType.PREDICT_MOCK_DETECTION:
            return self._execute_predict_mock_detection(job)

        if job.type == JobType.EVALUATE_DETECTION:
            return self._execute_evaluate_detection(job)

        raise ValueError(f"Unsupported job type: {job.type}")

    def _execute_ingest_nuscenes(self, job: JobManifest) -> dict[str, Any]:
        dataset_id = self._resolve_dataset_id(job)
        dataset_version = self._resolve_dataset_version(job)

        params = job.params
        max_scenes = params.get("maxScenes")
        mode = params.get("mode", "upsert")

        ingest_nuscenes(
            dataroot=self.context.raw_data_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            manifest_root=self.context.manifest_root,
            max_scenes=max_scenes,
            mode=IngestMode(mode),
        )

        return {
            "datasetId": dataset_id,
            "datasetVersion": dataset_version,
            "maxScenes": max_scenes,
            "mode": mode,
        }

    def _execute_predict_mock_detection(self, job: JobManifest) -> dict[str, Any]:
        dataset_id = self._resolve_dataset_id(job)
        dataset_version = self._resolve_dataset_version(job)

        params = job.params

        model_id = params.get("modelId", "centerpoint-mock")
        model_version = params.get("modelVersion", "v0")
        run_id = params.get("runId", default_inference_run_id(job.jobId))
        max_samples = params.get("maxSamples")
        seed = params.get("seed", 42)

        generate_mock_predictions(
            manifest_root=self.context.manifest_root,
            runs_root=self.context.runs_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            model_id=model_id,
            model_version=model_version,
            run_id=run_id,
            max_samples=max_samples,
            seed=seed,
        )

        return {
            "datasetId": dataset_id,
            "datasetVersion": dataset_version,
            "modelId": model_id,
            "modelVersion": model_version,
            "runId": run_id,
            "maxSamples": max_samples,
            "seed": seed,
        }

    def _execute_evaluate_detection(self, job: JobManifest) -> dict[str, Any]:
        dataset_id = self._resolve_dataset_id(job)
        dataset_version = self._resolve_dataset_version(job)

        params = job.params

        inference_run_id = params.get("inferenceRunId")
        evaluation_run_id = params.get(
            "evaluationRunId",
            default_evaluation_run_id(job.jobId),
        )
        match_distance_m = float(params.get("matchDistanceM", 2.0))

        if inference_run_id is None:
            raise ValueError("params.inferenceRunId is required for EVALUATE_DETECTION")

        evaluate_detection_run(
            manifest_root=self.context.manifest_root,
            runs_root=self.context.runs_root,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            inference_run_id=inference_run_id,
            evaluation_run_id=evaluation_run_id,
            match_distance_m=match_distance_m,
        )

        return {
            "datasetId": dataset_id,
            "datasetVersion": dataset_version,
            "inferenceRunId": inference_run_id,
            "evaluationRunId": evaluation_run_id,
            "matchDistanceM": match_distance_m,
        }

    def _resolve_dataset_id(self, job: JobManifest) -> str:
        return job.datasetId or self.context.default_dataset_id

    def _resolve_dataset_version(self, job: JobManifest) -> str:
        return job.datasetVersion or self.context.default_dataset_version
