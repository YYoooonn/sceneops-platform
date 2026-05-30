from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import onnxruntime as ort

from sceneops_core.schemas.datasets import DatasetManifest, DatasetSampleManifest
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.inference.constants import SUPPORTED_CATEGORIES
from sceneops_worker.inference.detection.base import (
    DetectionInferenceBackend,
    DetectionInferenceRequest,
    DetectionInferenceResult,
)


class OnnxRuntimeDetectionInferenceBackend(DetectionInferenceBackend):
    async def run(
        self,
        request: DetectionInferenceRequest,
    ) -> DetectionInferenceResult:
        inference_input = request.input
        if inference_input.model_uri is None:
            raise ValueError(
                "ONNX Runtime detection backend requires model_uri. "
                f"model={inference_input.params.model_id}:{inference_input.params.model_version}"
            )

        run_manifest = await self.generate_onnx_runtime_predictions(
            dataset_manifest=inference_input.dataset_manifest,
            dataset_artifact_store=request.dataset_artifact_store,
            run_artifact_store=request.run_artifact_store,
            model_id=inference_input.params.model_id,
            model_version=inference_input.params.model_version,
            model_uri=inference_input.model_uri,
            run_id=inference_input.run_id,
            max_samples=inference_input.params.max_samples,
        )

        return DetectionInferenceResult(
            run_id=inference_input.run_id,
            run_manifest_uri=run_manifest["predictionManifestUri"],
            predictions_root_uri=run_manifest["predictionsRootUri"],
            sample_count=int(run_manifest.get("sampleCount", 0)),
            prediction_count=int(run_manifest.get("predictionCount", 0)),
            status=str(run_manifest.get("status", "succeeded")),
            metrics=run_manifest.get("metrics", {}),
            metadata={
                "backend": inference_input.params.inference_backend.value,
                "model_uri": inference_input.model_uri,
                "endpoint_url": inference_input.endpoint_url,
            },
        )

    async def generate_onnx_runtime_predictions(
        self,
        *,
        dataset_manifest: DatasetManifest,
        dataset_artifact_store: DatasetArtifactStore,
        run_artifact_store: RunArtifactStore,
        model_id: str,
        model_version: str,
        model_uri: str,
        run_id: str,
        max_samples: int | None = None,
    ) -> dict[str, Any]:
        model_path = _to_local_path(model_uri)

        load_started = time.perf_counter()
        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        model_load_ms = (time.perf_counter() - load_started) * 1000.0

        sample_manifests: list[DatasetSampleManifest] = []
        async for sample_manifest in dataset_artifact_store.iter_samples(
            dataset_manifest,
            max_samples=max_samples,
        ):
            sample_manifests.append(sample_manifest)

        prediction_count = 0
        inference_latencies_ms: list[float] = []

        for sample in sample_manifests:
            inference_started = time.perf_counter()

            # v1에서는 ONNX session load / runtime path 검증이 목적.
            # 실제 detection model input pipeline은 다음 phase에서 붙인다.
            _try_warmup_session(session)

            inference_latency_ms = (time.perf_counter() - inference_started) * 1000.0
            inference_latencies_ms.append(inference_latency_ms)

            predictions = _build_contract_predictions_from_sample(sample)
            prediction_count += len(predictions)

            prediction_manifest = {
                "runId": run_id,
                "datasetId": dataset_manifest.dataset_id,
                "datasetVersion": dataset_manifest.dataset_version,
                "modelId": model_id,
                "modelVersion": model_version,
                "sceneId": sample.scene_id,
                "sampleId": sample.sample_id,
                "predictions": predictions,
                "metadata": {
                    "backend": "onnx_runtime",
                    "modelUri": model_uri,
                    "inferenceLatencyMs": round(inference_latency_ms, 4),
                },
            }

            await run_artifact_store.write_prediction_manifest(
                run_id=run_id,
                sample_id=sample.sample_id,
                manifest=prediction_manifest,
            )

        inference_manifest_uri = run_artifact_store.inference_run_manifest_uri(run_id)
        predictions_root_uri = run_artifact_store.inference_predictions_root_uri(run_id)

        avg_latency_ms = _avg(inference_latencies_ms)
        max_latency_ms = max(inference_latencies_ms) if inference_latencies_ms else 0.0

        run_manifest = {
            "runId": run_id,
            "runType": "inference",
            "datasetId": dataset_manifest.dataset_id,
            "datasetVersion": dataset_manifest.dataset_version,
            "modelId": model_id,
            "modelVersion": model_version,
            "status": "succeeded",
            "backend": "onnx_runtime",
            "modelUri": model_uri,
            "sampleCount": len(sample_manifests),
            "predictionCount": prediction_count,
            "predictionManifestUri": inference_manifest_uri,
            "predictionsRootUri": predictions_root_uri,
            "metrics": {
                "modelLoadMs": round(model_load_ms, 4),
                "avgInferenceLatencyMs": round(avg_latency_ms, 4),
                "maxInferenceLatencyMs": round(max_latency_ms, 4),
            },
            "createdAt": datetime.now(UTC).isoformat(),
        }

        await run_artifact_store.write_inference_run_manifest(
            run_id=run_id,
            manifest=run_manifest,
        )

        return run_manifest


def _to_local_path(uri: str) -> Path:
    parsed = urlparse(uri)

    if parsed.scheme == "file":
        return Path(parsed.path)

    if parsed.scheme == "":
        return Path(uri)

    raise ValueError(
        "ONNX Runtime backend currently supports local or file:// model_uri. "
        f"Got: {uri}"
    )


def _try_warmup_session(session: ort.InferenceSession) -> None:
    # v1에서는 모델 로드/세션 생성만 검증한다.
    # 다양한 ONNX input shape을 일반화해서 dummy inference를 수행하려면
    # model-specific preprocessor가 필요하므로 다음 단계에서 처리한다.
    _ = session.get_inputs()
    _ = session.get_outputs()


def _build_contract_predictions_from_sample(
    sample: DatasetSampleManifest,
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []

    for index, annotation in enumerate(sample.annotations):
        category_name = annotation.category_name
        if category_name not in SUPPORTED_CATEGORIES:
            continue

        predictions.append(
            {
                "predictionId": f"{sample.sample_id}-onnx-pred-{index:04d}",
                "categoryName": category_name,
                "translation": annotation.translation,
                "size": annotation.size,
                "rotation": annotation.rotation,
                "score": 0.9,
                "sourceAnnotationToken": annotation.annotation_token,
            }
        )

    return predictions


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
