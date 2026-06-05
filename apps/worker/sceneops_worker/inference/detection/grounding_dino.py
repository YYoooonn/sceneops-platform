from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_worker.inference.detection.base import (
    DetectionInferenceRequest,
    DetectionInferenceResult,
)
from sceneops_worker.inference.detection.frustum_lifting import frustum_lift

CAMERA_CHANNEL = "CAM_FRONT"


class GroundingDinoDetectionBackend:
    """HTTP client that delegates 2D detection to the inference server.

    The inference server (apps/inference-server) runs GroundingDINO-T on GPU.
    This backend sends image paths over HTTP and receives 2D bounding boxes.
    Frustum-based 3D lifting (step 2) will be added here, running on CPU
    using the LiDAR point cloud from the artifact store.

    endpoint_url is read from DetectionInferenceInput.endpoint_url, which
    comes from the job params or the model version registry.
    """

    def __init__(
        self,
        *,
        box_threshold: float = 0.35,
        text_threshold: float = 0.25,
        max_image_size: int = 800,
        camera_channel: str = CAMERA_CHANNEL,
        http_timeout: float = 120.0,
    ) -> None:
        self._box_threshold = box_threshold
        self._text_threshold = text_threshold
        self._max_image_size = max_image_size
        self._camera_channel = camera_channel
        self._http_timeout = http_timeout

    @property
    def backend_type(self) -> str:
        return InferenceBackendType.GROUNDING_DINO.value

    async def run(
        self,
        request: DetectionInferenceRequest,
    ) -> DetectionInferenceResult:
        inference_input = request.input
        endpoint_url = (inference_input.config.endpoint_url or "").rstrip("/")
        if not endpoint_url:
            raise ValueError(
                "GroundingDINO backend requires endpoint_url pointing to the "
                "inference server (e.g. http://inference-server:8001). "
                "Set it in the model version registry or job params."
            )

        dataset_manifest = inference_input.dataset_manifest
        run_id = inference_input.run_id
        raw_root = dataset_manifest.uris.raw_root
        if raw_root is None:
            raise ValueError(
                "GroundingDINO backend requires dataset_manifest.uris.raw_root "
                "to locate raw image files."
            )

        sample_manifests: list[SceneSampleManifest] = []
        async for sample in request.dataset_artifact_store.iter_samples(
            dataset_manifest,
            max_samples=inference_input.config.max_samples,
        ):
            sample_manifests.append(sample)

        prediction_count = 0
        latencies_ms: list[float] = []

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            for sample in sample_manifests:
                sensor = sample.sensors.get(self._camera_channel)
                if sensor is None:
                    predictions: list[dict[str, Any]] = []
                else:
                    image_path = _resolve_raw_path(raw_root, sensor.filename)
                    t0 = time.perf_counter()
                    detections_2d = await _call_inference_server(
                        client=client,
                        endpoint_url=endpoint_url,
                        image_path=image_path,
                        box_threshold=self._box_threshold,
                        text_threshold=self._text_threshold,
                        max_image_size=self._max_image_size,
                    )
                    latencies_ms.append((time.perf_counter() - t0) * 1000.0)

                    lidar_sensor = sample.sensors.get("LIDAR_TOP")
                    predictions = _build_predictions(
                        sample=sample,
                        detections_2d=detections_2d,
                        camera_sensor=sensor,
                        lidar_sensor=lidar_sensor,
                        raw_root=raw_root,
                        max_image_size=self._max_image_size,
                    )

                prediction_count += len(predictions)
                await request.run_artifact_store.write_prediction_manifest(
                    run_id=run_id,
                    sample_id=sample.sample_id,
                    manifest=_prediction_manifest(
                        run_id=run_id,
                        dataset_manifest=dataset_manifest,
                        model_id=inference_input.config.model_id,
                        model_version=inference_input.config.model_version,
                        sample=sample,
                        predictions=predictions,
                        camera_channel=self._camera_channel,
                        endpoint_url=endpoint_url,
                    ),
                )

        run_manifest_uri = request.run_artifact_store.inference_run_manifest_uri(run_id)
        predictions_root_uri = (
            request.run_artifact_store.inference_predictions_root_uri(run_id)
        )
        avg_ms = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0

        run_manifest = {
            "run_id": run_id,
            "run_type": "inference",
            "dataset_id": dataset_manifest.dataset_id,
            "dataset_version": dataset_manifest.dataset_version,
            "model_id": inference_input.config.model_id,
            "model_version": inference_input.config.model_version,
            "status": "succeeded",
            "backend": self.backend_type,
            "endpoint_url": endpoint_url,
            "sample_count": len(sample_manifests),
            "prediction_count": prediction_count,
            "prediction_manifest_uri": run_manifest_uri,
            "predictions_root_uri": predictions_root_uri,
            "metrics": {
                "avg_roundtrip_ms": round(avg_ms, 2),
                "camera_channel": self._camera_channel,
            },
            "created_at": datetime.now(UTC).isoformat(),
        }

        await request.run_artifact_store.write_inference_run_manifest(
            run_id=run_id,
            manifest=run_manifest,
        )

        return DetectionInferenceResult(
            run_id=run_id,
            run_manifest_uri=run_manifest_uri,
            predictions_root_uri=predictions_root_uri,
            sample_count=len(sample_manifests),
            prediction_count=prediction_count,
            status="succeeded",
            metrics=run_manifest["metrics"],
            metadata={"backend": self.backend_type, "endpoint_url": endpoint_url},
        )


async def _call_inference_server(
    *,
    client: httpx.AsyncClient,
    endpoint_url: str,
    image_path: Path,
    box_threshold: float,
    text_threshold: float,
    max_image_size: int,
) -> list[dict[str, Any]]:
    response = await client.post(
        f"{endpoint_url}/v1/detect",
        json={
            "image_path": str(image_path),
            "box_threshold": box_threshold,
            "text_threshold": text_threshold,
            "max_image_size": max_image_size,
        },
    )
    response.raise_for_status()
    return response.json()["detections"]


def _build_predictions(
    *,
    sample: SceneSampleManifest,
    detections_2d: list[dict[str, Any]],
    camera_sensor: Any,
    lidar_sensor: Any,
    raw_root: str,
    max_image_size: int,
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for i, det in enumerate(detections_2d):
        bbox_2d: list[float] = det["bbox_2d"]

        lift: dict[str, Any] | None = None
        if lidar_sensor is not None and lidar_sensor.filename:
            try:
                lift = frustum_lift(
                    bbox_2d=bbox_2d,
                    camera_sensor=camera_sensor,
                    lidar_sensor=lidar_sensor,
                    raw_root=raw_root,
                    max_image_size=max_image_size,
                )
            except Exception:
                pass  # keep placeholder on any lifting failure

        predictions.append(
            {
                "prediction_id": f"{sample.sample_id}-gdino-{i:04d}",
                "category_name": det["category_name"],
                "translation": lift["translation"] if lift else [0.0, 0.0, 0.0],
                "size": lift["size"] if lift else [1.0, 1.0, 1.0],
                "rotation": lift["rotation"] if lift else [1.0, 0.0, 0.0, 0.0],
                "score": det["score"],
                "source_annotation_token": None,
                "bbox_2d": bbox_2d,
                "lifting_method": lift["lifting_method"] if lift else "none",
                "cluster_point_count": lift.get("cluster_point_count")
                if lift
                else None,
            }
        )
    return predictions


def _prediction_manifest(
    *,
    run_id: str,
    dataset_manifest: DatasetManifest,
    model_id: str,
    model_version: str,
    sample: SceneSampleManifest,
    predictions: list[dict[str, Any]],
    camera_channel: str,
    endpoint_url: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "dataset_id": dataset_manifest.dataset_id,
        "dataset_version": dataset_manifest.dataset_version,
        "model_id": model_id,
        "model_version": model_version,
        "scene_id": sample.scene_id,
        "sample_id": sample.sample_id,
        "predictions": predictions,
        "metadata": {
            "backend": InferenceBackendType.GROUNDING_DINO.value,
            "camera_channel": camera_channel,
            "endpoint_url": endpoint_url,
        },
    }


def _resolve_raw_path(raw_root: str, filename: str) -> Path:
    parsed = urlparse(raw_root)
    if parsed.scheme == "file":
        base = Path(parsed.path)
    elif parsed.scheme == "":
        base = Path(raw_root)
    else:
        raise ValueError(
            f"GroundingDINO backend supports local raw_root only. Got: {raw_root!r}"
            "\nTODO: add MinIO/S3 image download for remote deployments."
        )
    return base / filename
