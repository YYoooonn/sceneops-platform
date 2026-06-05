from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_worker.inference.detection.base import (
    DetectionInferenceBackend,
    DetectionInferenceRequest,
    DetectionInferenceResult,
)

# from sceneops_worker.inference.mock_detection import generate_mock_predictions
from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.inference.constants import SUPPORTED_CATEGORIES


class MockDetectionInferenceBackend(DetectionInferenceBackend):
    @property
    def backend_type(self) -> str:
        return InferenceBackendType.MOCK.value

    async def run(
        self,
        request: DetectionInferenceRequest,
    ) -> DetectionInferenceResult:
        inference_input = request.input

        run_manifest = await self.generate_mock_predictions(
            dataset_manifest=inference_input.dataset_manifest,
            dataset_artifact_store=request.dataset_artifact_store,
            run_artifact_store=request.run_artifact_store,
            model_id=inference_input.config.model_id,
            model_version=inference_input.config.model_version,
            run_id=inference_input.run_id,
            max_samples=inference_input.config.max_samples,
        )

        return DetectionInferenceResult(
            run_id=inference_input.run_id,
            run_manifest_uri=run_manifest["prediction_manifest_uri"],
            predictions_root_uri=run_manifest["predictions_root_uri"],
            sample_count=int(run_manifest.get("sample_count", 0)),
            prediction_count=int(run_manifest.get("prediction_count", 0)),
            status=str(run_manifest.get("status", "succeeded")),
            metrics=run_manifest.get("metrics", {}),
            metadata={
                "backend": inference_input.config.inference_backend,
                "model_uri": inference_input.config.model_uri,
                "endpoint_url": inference_input.config.endpoint_url,
            },
        )

    async def generate_mock_predictions(
        self,
        *,
        dataset_manifest: DatasetManifest,
        dataset_artifact_store: DatasetArtifactStore,
        run_artifact_store: RunArtifactStore,
        model_id: str,
        model_version: str,
        run_id: str,
        max_samples: int | None = None,
        seed: int = 42,
    ) -> dict[str, Any]:
        random.seed(seed)

        sample_manifests: list[SceneSampleManifest] = []
        async for sample_manifest in dataset_artifact_store.iter_samples(
            dataset_manifest,
            max_samples=max_samples,
        ):
            sample_manifests.append(sample_manifest)

        prediction_count = 0

        for sample in sample_manifests:
            predictions = _build_predictions_from_sample(sample)
            prediction_count += len(predictions)

            prediction_manifest = {
                "run_id": run_id,
                "dataset_id": dataset_manifest.dataset_id,
                "dataset_version": dataset_manifest.dataset_version,
                "model_id": model_id,
                "model_version": model_version,
                "scene_id": sample.scene_id,
                "sample_id": sample.sample_id,
                "predictions": predictions,
            }

            await run_artifact_store.write_prediction_manifest(
                run_id=run_id,
                sample_id=sample.sample_id,
                manifest=prediction_manifest,
            )

        inference_manifest_uri = run_artifact_store.inference_run_manifest_uri(run_id)
        predictions_root_uri = run_artifact_store.inference_predictions_root_uri(run_id)

        run_manifest = {
            "run_id": run_id,
            "run_type": "inference",
            "dataset_id": dataset_manifest.dataset_id,
            "dataset_version": dataset_manifest.dataset_version,
            "model_id": model_id,
            "model_version": model_version,
            "status": "succeeded",
            "sample_count": len(sample_manifests),
            "prediction_count": prediction_count,
            "prediction_manifest_uri": inference_manifest_uri,
            "predictions_root_uri": predictions_root_uri,
            "created_at": datetime.now(UTC).isoformat(),
        }

        await run_artifact_store.write_inference_run_manifest(
            run_id=run_id,
            manifest=run_manifest,
        )

        return run_manifest


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
