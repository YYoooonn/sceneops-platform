from __future__ import annotations

import random
from typing import Any

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


class MockDetectionInferenceBackend(DetectionInferenceBackend):
    @property
    def backend_type(self) -> str:
        return InferenceBackendType.MOCK.value

    async def run(
        self,
        request: DetectionInferenceRequest,
    ) -> DetectionInferenceResult:
        inference_input = request.input

        prediction_manifest = await self.generate_mock_predictions(
            dataset_manifest=inference_input.dataset_manifest,
            scene_artifact_store=request.scene_artifact_store,
            run_artifact_store=request.run_artifact_store,
            model_id=inference_input.config.model_id,
            model_version=inference_input.config.model_version,
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

    async def generate_mock_predictions(
        self,
        *,
        dataset_manifest: DatasetManifest,
        scene_artifact_store: SceneArtifactStore,
        run_artifact_store: RunArtifactStore,
        model_id: str,
        model_version: str,
        run_id: str,
        max_samples: int | None = None,
        seed: int = 42,
    ) -> DetectionPredictionManifest:
        random.seed(seed)

        sample_manifests: list[SceneSampleManifest] = []
        async for sample_manifest in scene_artifact_store.iter_samples(
            dataset_manifest,
            max_samples=max_samples,
        ):
            sample_manifests.append(sample_manifest)

        prediction_count = 0
        prediction_shards: list[DetectionPredictionShardRef] = []

        for sample in sample_manifests:
            predictions = _build_predictions_from_sample(sample)
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
                    "backend": InferenceBackendType.MOCK.value,
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

        predictions_root_uri = run_artifact_store.inference_predictions_root_uri(run_id)
        prediction_manifest_uri = run_artifact_store.inference_prediction_manifest_uri(
            run_id
        )
        run_manifest_uri = run_artifact_store.inference_run_manifest_uri(run_id)

        metrics = {
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
            inference_backend=InferenceBackendType.MOCK.value,
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
                "backend": InferenceBackendType.MOCK.value,
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
                "backend": InferenceBackendType.MOCK.value,
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


def _build_predictions_from_sample(
    sample: SceneSampleManifest,
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []

    for index, annotation in enumerate(sample.annotations):
        category_name = annotation.category

        if category_name not in SUPPORTED_CATEGORIES:
            continue

        if random.random() < 0.15:
            continue

        translation = _perturb_translation(annotation.translation)
        size = _perturb_size(annotation.size)

        predictions.append(
            {
                "prediction_id": f"{sample.sample_id}-pred-{index:04d}",
                "category_name": category_name,
                "translation": translation,
                "size": size,
                "rotation": annotation.rotation,
                "score": round(random.uniform(0.55, 0.98), 4),
                "source_annotation_token": annotation.annotation_id,
            }
        )

    if random.random() < 0.25:
        predictions.append(_build_false_positive(sample.sample_id))

    return predictions


def _perturb_translation(translation: list[float]) -> list[float]:
    return [
        round(translation[0] + random.uniform(-0.8, 0.8), 4),
        round(translation[1] + random.uniform(-0.8, 0.8), 4),
        round(translation[2] + random.uniform(-0.2, 0.2), 4),
    ]


def _perturb_size(size: list[float]) -> list[float]:
    return [
        round(max(0.1, size[0] + random.uniform(-0.2, 0.2)), 4),
        round(max(0.1, size[1] + random.uniform(-0.2, 0.2)), 4),
        round(max(0.1, size[2] + random.uniform(-0.2, 0.2)), 4),
    ]


def _build_false_positive(sample_id: str) -> dict[str, Any]:
    return {
        "prediction_id": f"{sample_id}-fp-0000",
        "category_name": "vehicle.car",
        "translation": [
            round(random.uniform(-30.0, 30.0), 4),
            round(random.uniform(-30.0, 30.0), 4),
            round(random.uniform(0.0, 2.0), 4),
        ],
        "size": [4.2, 1.8, 1.6],
        "rotation": [1.0, 0.0, 0.0, 0.0],
        "score": round(random.uniform(0.3, 0.7), 4),
        "source_annotation_token": None,
    }
