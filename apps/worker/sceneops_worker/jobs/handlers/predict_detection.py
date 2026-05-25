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
        if params.inferenceBackend == InferenceBackend.MOCK:
            return self._run_mock_detection(params=params, job=job)

        # if params.inferenceBackend == InferenceBackend.ONNX_RUNTIME:
        #     return self._run_onnx_runtime(params=params)

        # if params.inferenceBackend == InferenceBackend.TRITON:
        #     return self._run_triton(params=params)

        raise ValueError(f"Unsupported inference backend: {params.inferenceBackend}")

    def _run_mock_detection(
        self, *, params: PredictDetectionJobParams, job: JobManifest
    ) -> PredictDetectionJobResult:
        inference_run_id = params.inferenceRunId or default_inference_run_id(job.jobId)

        run_manifest = generate_mock_predictions(
            manifest_root=self.context.manifest_root,
            runs_root=self.context.runs_root,
            dataset_id=params.datasetId,
            dataset_version=params.datasetVersion,
            model_id=params.modelId,
            model_version=params.modelVersion,
            run_id=inference_run_id,
            max_samples=params.maxSamples,
        )

        prediction_manifest_uri = run_manifest.get("predictionManifestUri") or str(
            self.context.runs_root / "inference" / inference_run_id / "run.json"
        )

        return PredictDetectionJobResult(
            datasetId=params.datasetId,
            datasetVersion=params.datasetVersion,
            modelId=params.modelId,
            modelVersion=params.modelVersion,
            inferenceRunId=inference_run_id,
            predictionManifestUri=prediction_manifest_uri,
            sampleCount=int(run_manifest.get("sampleCount", 0)),
            resultSummary={
                "predictionCount": run_manifest.get("predictionCount", 0),
                "status": run_manifest.get("status"),
                "predictionsRootUri": run_manifest.get("predictionsRootUri"),
                "createdAt": run_manifest.get("createdAt"),
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
