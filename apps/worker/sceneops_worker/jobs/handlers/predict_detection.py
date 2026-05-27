from __future__ import annotations

from sceneops_core.ids.runs import default_inference_run_id
from sceneops_core.schemas.jobs import (
    InferenceBackend,
    JobManifest,
    JobType,
    PredictDetectionJobParams,
    PredictDetectionJobResult,
)
from sceneops_worker.jobs.handlers.base import TypedJobHandler
from sceneops_worker.predictions.mock_detection import generate_mock_predictions


class PredictDetectionJobHandler(
    TypedJobHandler[PredictDetectionJobParams, PredictDetectionJobResult]
):
    job_type = JobType.PREDICT_DETECTION

    def parse_params(self, job: JobManifest) -> PredictDetectionJobParams:
        return PredictDetectionJobParams.model_validate(job.params)

    def run(
        self,
        *,
        params: PredictDetectionJobParams,
        job: JobManifest,
    ) -> PredictDetectionJobResult:
        if params.inference_backend == InferenceBackend.MOCK:
            return self._run_mock_detection(params=params, job=job)

        # if params.inference_backend == InferenceBackend.ONNX_RUNTIME:
        #     return self._run_onnx_runtime(params=params)

        # if params.inference_backend == InferenceBackend.TRITON:
        #     return self._run_triton(params=params)

        raise ValueError(f"Unsupported inference backend: {params.inference_backend}")

    def _run_mock_detection(
        self, *, params: PredictDetectionJobParams, job: JobManifest
    ) -> PredictDetectionJobResult:
        inference_run_id = params.inference_run_id or default_inference_run_id(
            job.job_id
        )

        run_manifest = generate_mock_predictions(
            manifest_root=self.context.manifest_root,
            runs_root=self.context.runs_root,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=params.model_id,
            model_version=params.model_version,
            run_id=inference_run_id,
            max_samples=params.max_samples,
        )

        prediction_manifest_uri = run_manifest.get("prediction_manifest_uri") or str(
            self.context.runs_root / "inference" / inference_run_id / "run.json"
        )

        return PredictDetectionJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            model_id=params.model_id,
            model_version=params.model_version,
            inference_run_id=inference_run_id,
            prediction_manifest_uri=prediction_manifest_uri,
            sample_count=int(run_manifest.get("sample_count", 0)),
            result_summary={
                "predictionCount": run_manifest.get("prediction_count", 0),
                "status": run_manifest.get("status"),
                "predictions_root_uri": run_manifest.get("predictions_root_uri"),
                "created_at": run_manifest.get("created_at"),
            },
        )

    def _run_onnx_runtime(
        self,
        *,
        params: PredictDetectionJobParams,
    ) -> PredictDetectionJobResult:
        raise NotImplementedError("ONNX Runtime inference is not implemented yet")

    def _run_triton(
        self,
        *,
        params: PredictDetectionJobParams,
    ) -> PredictDetectionJobResult:
        raise NotImplementedError("Triton inference is not implemented yet")
