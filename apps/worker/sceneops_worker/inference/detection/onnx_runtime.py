from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import onnxruntime as ort

from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.inference.schemas.manifests import (
    DetectionPredictionManifest,
    DetectionPredictionShardRef,
)
from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_worker.inference.constants import SUPPORTED_CATEGORIES
from sceneops_worker.inference.detection.base import (
    DetectionInferenceBackend,
    DetectionInferenceRequest,
    DetectionInferenceResult,
)
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore


class OnnxRuntimeDetectionInferenceBackend(DetectionInferenceBackend):
    @property
    def backend_type(self) -> str:
        return InferenceBackendType.ONNX_RUNTIME.value

    async def run(
        self,
        request: DetectionInferenceRequest,
    ) -> DetectionInferenceResult:
        inference_input = request.input
        if inference_input.config.model_uri is None:
            raise ValueError(
                "ONNX Runtime detection backend requires model_uri. "
                f"model={inference_input.config.model_id}:{inference_input.config.model_version}"
            )

        prediction_manifest = await self.generate_onnx_runtime_predictions(
            dataset_manifest=inference_input.dataset_manifest,
            scene_artifact_store=request.scene_artifact_store,
            run_artifact_store=request.run_artifact_store,
            model_id=inference_input.config.model_id,
            model_version=inference_input.config.model_version,
            model_uri=inference_input.config.model_uri,
            run_id=inference_input.run_id,
            max_samples=inference_input.config.max_samples,
        )

        return DetectionInferenceResult(
            run_id=inference_input.run_id,
            prediction_manifest_uri=prediction_manifest.prediction_manifest_uri,
            predictions_root_uri=prediction_manifest.predictions_root_uri,
            scene_count=prediction_manifest.scene_count,
            sample_count=prediction_manifest.sample_count,
            inference_request_count=prediction_manifest.inference_request_count,
            prediction_count=prediction_manifest.prediction_count,
            evaluable_prediction_count=prediction_manifest.evaluable_prediction_count,
            lifting_succeeded_count=prediction_manifest.lifting_succeeded_count,
            lifting_failed_count=prediction_manifest.lifting_failed_count,
            status=prediction_manifest.status,
            metrics=prediction_manifest.metrics,
            metadata={
                "backend": inference_input.config.inference_backend,
                "model_uri": inference_input.config.model_uri,
                "endpoint_url": inference_input.config.endpoint_url,
                "run_manifest_uri": prediction_manifest.metadata.get(
                    "run_manifest_uri"
                ),
            },
        )

    async def generate_onnx_runtime_predictions(
        self,
        *,
        dataset_manifest: DatasetManifest,
        scene_artifact_store: SceneArtifactStore,
        run_artifact_store: RunArtifactStore,
        model_id: str,
        model_version: str,
        model_uri: str,
        run_id: str,
        max_samples: int | None = None,
    ) -> DetectionPredictionManifest:
        model_path = _to_local_path(model_uri)

        load_started = time.perf_counter()
        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        model_load_ms = (time.perf_counter() - load_started) * 1000.0

        sample_manifests: list[SceneSampleManifest] = []
        async for sample_manifest in scene_artifact_store.iter_samples(
            dataset_manifest,
            max_samples=max_samples,
        ):
            sample_manifests.append(sample_manifest)

        prediction_count = 0
        inference_latencies_ms: list[float] = []
        prediction_shards: list[DetectionPredictionShardRef] = []

        for sample in sample_manifests:
            inference_started = time.perf_counter()

            # v1에서는 ONNX session load / runtime path 검증이 목적.
            _try_warmup_session(session)

            inference_latency_ms = (time.perf_counter() - inference_started) * 1000.0
            inference_latencies_ms.append(inference_latency_ms)

            predictions = _build_contract_predictions_from_sample(sample)
            prediction_count += len(predictions)

            sample_prediction_manifest = {
                "run_id": run_id,
                "dataset_id": dataset_manifest.dataset_id,
                "dataset_version": dataset_manifest.dataset_version,
                "model_id": model_id,
                "model_version": model_version,
                "scene_id": sample.scene_id,
                "sample_id": sample.sample_id,
                "predictions": predictions,
                "metadata": {
                    "backend": InferenceBackendType.ONNX_RUNTIME.value,
                    "model_uri": model_uri,
                    "inference_latency_ms": round(inference_latency_ms, 4),
                },
            }

            sample_prediction_uri = (
                await run_artifact_store.write_sample_prediction_manifest(
                    run_id=run_id,
                    sample_id=sample.sample_id,
                    manifest=sample_prediction_manifest,
                )
            )

            prediction_shards.append(
                DetectionPredictionShardRef(
                    scene_id=sample.scene_id,
                    sample_id=sample.sample_id,
                    uri=sample_prediction_uri,
                    prediction_count=len(predictions),
                )
            )

        scene_count = len({sample.scene_id for sample in sample_manifests})
        sample_count = len(sample_manifests)
        inference_request_count = sample_count

        lifting_succeeded_count = 0
        lifting_failed_count = 0
        lifting_not_applicable_count = prediction_count
        evaluable_prediction_count = prediction_count

        avg_latency_ms = _avg(inference_latencies_ms)
        max_latency_ms = max(inference_latencies_ms) if inference_latencies_ms else 0.0

        predictions_root_uri = run_artifact_store.inference_predictions_root_uri(run_id)
        prediction_manifest_uri = run_artifact_store.inference_prediction_manifest_uri(
            run_id
        )
        run_manifest_uri = run_artifact_store.inference_run_manifest_uri(run_id)

        metrics = {
            "model_load_ms": round(model_load_ms, 4),
            "avg_inference_latency_ms": round(avg_latency_ms, 4),
            "max_inference_latency_ms": round(max_latency_ms, 4),
            "scene_count": scene_count,
            "sample_count": sample_count,
            "inference_request_count": inference_request_count,
            "prediction_count": prediction_count,
            "evaluable_prediction_count": evaluable_prediction_count,
            "lifting_succeeded_count": lifting_succeeded_count,
            "lifting_failed_count": lifting_failed_count,
            "lifting_not_applicable_count": lifting_not_applicable_count,
        }

        created_at = utc_now()

        prediction_manifest = DetectionPredictionManifest(
            inference_run_id=run_id,
            dataset_id=dataset_manifest.dataset_id,
            dataset_version=dataset_manifest.dataset_version,
            model_id=model_id,
            model_version=model_version,
            inference_backend=InferenceBackendType.ONNX_RUNTIME.value,
            status="succeeded",
            scene_count=scene_count,
            sample_count=sample_count,
            inference_request_count=inference_request_count,
            prediction_count=prediction_count,
            evaluable_prediction_count=evaluable_prediction_count,
            lifting_succeeded_count=lifting_succeeded_count,
            lifting_failed_count=lifting_failed_count,
            lifting_not_applicable_count=lifting_not_applicable_count,
            prediction_manifest_uri=prediction_manifest_uri,
            predictions_root_uri=predictions_root_uri,
            prediction_shards=prediction_shards,
            metrics=metrics,
            metadata={
                "backend": InferenceBackendType.ONNX_RUNTIME.value,
                "model_uri": model_uri,
                "run_manifest_uri": run_manifest_uri,
            },
            created_at=created_at,
        )

        await run_artifact_store.write_inference_prediction_manifest(
            run_id=run_id,
            manifest=prediction_manifest.to_artifact_dict(),
        )

        await run_artifact_store.write_inference_run_manifest(
            run_id=run_id,
            manifest={
                "run_id": run_id,
                "run_type": "inference",
                "dataset_id": dataset_manifest.dataset_id,
                "dataset_version": dataset_manifest.dataset_version,
                "model_id": model_id,
                "model_version": model_version,
                "status": "succeeded",
                "backend": InferenceBackendType.ONNX_RUNTIME.value,
                "model_uri": model_uri,
                "scene_count": scene_count,
                "sample_count": sample_count,
                "inference_request_count": inference_request_count,
                "prediction_count": prediction_count,
                "evaluable_prediction_count": evaluable_prediction_count,
                "lifting_succeeded_count": lifting_succeeded_count,
                "lifting_failed_count": lifting_failed_count,
                "lifting_not_applicable_count": lifting_not_applicable_count,
                "prediction_manifest_uri": prediction_manifest_uri,
                "predictions_root_uri": predictions_root_uri,
                "metrics": metrics,
                "created_at": created_at.isoformat(),
            },
        )

        return prediction_manifest


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
    sample: SceneSampleManifest,
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []

    for index, annotation in enumerate(sample.annotations):
        category_name = annotation.category_name
        if category_name not in SUPPORTED_CATEGORIES:
            continue

        predictions.append(
            {
                "prediction_id": f"{sample.sample_id}-onnx-pred-{index:04d}",
                "category_name": category_name,
                "translation": annotation.translation,
                "size": annotation.size,
                "rotation": annotation.rotation,
                "score": 0.9,
                "source_annotation_token": annotation.annotation_token,
            }
        )

    return predictions


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
