from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from sceneops_core.common.time import utc_now
from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.inference.enums import InferenceBackendType
from sceneops_core.inference.schemas import DetectionPredictionManifest
from sceneops_core.inference.schemas.manifests import DetectionPredictionShardRef
from sceneops_worker.inference.detection.base import (
    DetectionInferenceRequest,
    DetectionInferenceResult,
    DetectionSampleInput,
)
from sceneops_worker.inference.detection.frustum_lifting import frustum_lift
from sceneops_worker.inference.detection.sample_selector import (
    DetectionSampleSelector,
    SampleSelectionConfig,
)

logger = logging.getLogger(__name__)

_DEFAULT_BOX_THRESHOLD = 0.35
_DEFAULT_TEXT_THRESHOLD = 0.25
_DEFAULT_MAX_IMAGE_SIZE = 800
_DEFAULT_CAMERA_CHANNEL = "CAM_FRONT"
_DEFAULT_HTTP_TIMEOUT = 120.0


class GroundingDinoDetectionBackend:
    """HTTP client that delegates 2D detection to the GroundingDINO inference server.

    Sends image_uri (file:// or future remote URI) to the inference server.
    The inference server resolves the URI and loads the image independently.

    Scene/sample selection is handled by DetectionSampleSelector — this backend
    does not traverse manifests or build artifact paths; it only issues HTTP
    requests and assembles prediction records.
    """

    def __init__(
        self,
        *,
        box_threshold: float = _DEFAULT_BOX_THRESHOLD,
        text_threshold: float = _DEFAULT_TEXT_THRESHOLD,
        max_image_size: int = _DEFAULT_MAX_IMAGE_SIZE,
        camera_channel: str = _DEFAULT_CAMERA_CHANNEL,
        http_timeout: float = _DEFAULT_HTTP_TIMEOUT,
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
        config = inference_input.config

        endpoint_url = (config.endpoint_url or "").rstrip("/")
        if not endpoint_url:
            raise ValueError(
                "GroundingDINO backend requires endpoint_url pointing to the "
                "inference server (e.g. http://sceneops-inference:8001). "
                "Set it via job params or model version registry."
            )

        raw_source_root_uri = config.raw_source_root_uri
        if not raw_source_root_uri:
            raise ValueError(
                "GroundingDINO backend requires raw_source_root_uri to resolve image URIs. "
                "Ensure the dataset version has raw_source_root_uri set."
            )

        # Config-driven inference params; fall back to constructor defaults if not set.
        box_threshold = (
            config.box_threshold
            if config.box_threshold is not None
            else self._box_threshold
        )
        text_threshold = (
            config.text_threshold
            if config.text_threshold is not None
            else self._text_threshold
        )
        max_image_size = (
            config.max_image_size
            if config.max_image_size is not None
            else self._max_image_size
        )
        camera_channel = config.camera_channel or self._camera_channel
        enable_3d_lifting = config.enable_3d_lifting

        # ── sample selection ───────────────────────────────────────────────────
        selector = DetectionSampleSelector()
        sample_inputs = await selector.select(
            dataset_manifest=inference_input.dataset_manifest,
            scene_artifact_store=request.scene_artifact_store,
            config=SampleSelectionConfig(
                dataset_id=inference_input.dataset_manifest.dataset_id,
                dataset_version=inference_input.dataset_manifest.dataset_version,
                camera_channel=camera_channel,
                raw_source_root_uri=raw_source_root_uri,
                scene_ids=config.scene_ids,
                max_scenes=config.max_scenes,
                max_samples=config.max_samples,
                enable_3d_lifting=enable_3d_lifting,
            ),
        )

        run_id = inference_input.run_id
        scene_ids = {s.scene_id for s in sample_inputs}
        prediction_count = 0
        lifting_succeeded_count = 0
        lifting_failed_count = 0
        lifting_not_applicable_count = 0
        latencies_ms: list[float] = []
        sample_prediction_uris: list[dict[str, Any]] = []

        # ── inference + prediction building ───────────────────────────────────
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            for sample_input in sample_inputs:
                t0 = time.perf_counter()
                detections_2d = await _call_inference_server(
                    client=client,
                    endpoint_url=endpoint_url,
                    image_uri=sample_input.image_uri,
                    box_threshold=box_threshold,
                    text_threshold=text_threshold,
                    max_image_size=max_image_size,
                    detection_prompt=config.detection_prompt,
                )
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)

                predictions = _build_predictions(
                    sample=sample_input,
                    detections_2d=detections_2d,
                    camera_sensor=sample_input.camera_sensor_frame,
                    lidar_sensor=sample_input.lidar_sensor_frame,
                    raw_root=raw_source_root_uri,
                    max_image_size=max_image_size,
                )

                prediction_count += len(predictions)
                for p in predictions:
                    status = p.get("lifting_status", "not_applicable")
                    if status == "succeeded":
                        lifting_succeeded_count += 1
                    elif status == "failed":
                        lifting_failed_count += 1
                    else:
                        lifting_not_applicable_count += 1

                sample_prediction_uri = (
                    await request.run_artifact_store.write_sample_prediction_manifest(
                        run_id=run_id,
                        sample_id=sample_input.sample_id,
                        manifest=_sample_prediction_manifest(
                            run_id=run_id,
                            dataset_manifest=inference_input.dataset_manifest,
                            model_id=config.model_id,
                            model_version=config.model_version,
                            sample_input=sample_input,
                            predictions=predictions,
                            endpoint_url=endpoint_url,
                        ),
                    )
                )

                sample_prediction_uris.append(
                    {
                        "scene_id": sample_input.scene_id,
                        "sample_id": sample_input.sample_id,
                        "uri": sample_prediction_uri,
                        "prediction_count": len(predictions),
                    }
                )

        # ── run manifest ───────────────────────────────────────────────────────
        created_at = utc_now()
        evaluable_prediction_count = prediction_count - lifting_failed_count
        avg_ms = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0
        inference_request_count = len(latencies_ms)

        predictions_root_uri = (
            request.run_artifact_store.inference_predictions_root_uri(run_id)
        )
        prediction_manifest_uri = (
            request.run_artifact_store.inference_prediction_manifest_uri(run_id)
        )
        prediction_manifest = DetectionPredictionManifest(
            inference_run_id=run_id,
            dataset_id=inference_input.dataset_manifest.dataset_id,
            dataset_version=inference_input.dataset_manifest.dataset_version,
            model_id=config.model_id,
            model_version=config.model_version,
            inference_backend=self.backend_type,
            status="succeeded",
            scene_count=len(scene_ids),
            sample_count=len(sample_inputs),
            inference_request_count=inference_request_count,
            prediction_count=prediction_count,
            evaluable_prediction_count=evaluable_prediction_count,
            lifting_succeeded_count=lifting_succeeded_count,
            lifting_failed_count=lifting_failed_count,
            lifting_not_applicable_count=lifting_not_applicable_count,
            prediction_manifest_uri=prediction_manifest_uri,
            predictions_root_uri=predictions_root_uri,
            prediction_shards=[
                DetectionPredictionShardRef.model_validate(item)
                for item in sample_prediction_uris
            ],
            metrics={
                "avg_roundtrip_ms": round(avg_ms, 2),
                "camera_channel": camera_channel,
                "box_threshold": box_threshold,
                "text_threshold": text_threshold,
                "max_image_size": max_image_size,
            },
            metadata={
                "backend": self.backend_type,
                "endpoint_url": endpoint_url,
            },
            created_at=utc_now(),
        )
        prediction_manifest_uri = (
            await request.run_artifact_store.write_inference_prediction_manifest(
                run_id=run_id,
                manifest=prediction_manifest.to_artifact_dict(),
            )
        )

        run_manifest = {
            "run_id": run_id,
            "run_type": "inference",
            "dataset_id": inference_input.dataset_manifest.dataset_id,
            "dataset_version": inference_input.dataset_manifest.dataset_version,
            "model_id": config.model_id,
            "model_version": config.model_version,
            "status": "succeeded",
            "backend": self.backend_type,
            "endpoint_url": endpoint_url,
            "scene_count": len(scene_ids),
            "sample_count": len(sample_inputs),
            "inference_request_count": inference_request_count,
            "prediction_count": prediction_count,
            "evaluable_prediction_count": evaluable_prediction_count,
            "lifting_succeeded_count": lifting_succeeded_count,
            "lifting_failed_count": lifting_failed_count,
            "prediction_manifest_uri": prediction_manifest_uri,
            "predictions_root_uri": predictions_root_uri,
            "metrics": {
                "avg_roundtrip_ms": round(avg_ms, 2),
                "camera_channel": camera_channel,
                "lifting_succeeded_count": lifting_succeeded_count,
                "lifting_failed_count": lifting_failed_count,
                "lifting_not_applicable_count": lifting_not_applicable_count,
                "evaluable_prediction_count": evaluable_prediction_count,
            },
            "created_at": created_at.isoformat(),
        }

        run_manifest_uri = (
            await request.run_artifact_store.write_inference_run_manifest(
                run_id=run_id,
                manifest=run_manifest,
            )
        )

        return DetectionInferenceResult(
            run_id=run_id,
            prediction_manifest_uri=prediction_manifest_uri,
            predictions_root_uri=predictions_root_uri,
            scene_count=len(scene_ids),
            sample_count=len(sample_inputs),
            inference_request_count=inference_request_count,
            prediction_count=prediction_count,
            evaluable_prediction_count=evaluable_prediction_count,
            lifting_succeeded_count=lifting_succeeded_count,
            lifting_failed_count=lifting_failed_count,
            status="succeeded",
            metrics=run_manifest["metrics"],
            metadata={
                "backend": self.backend_type,
                "endpoint_url": endpoint_url,
                "run_manifest_uri": run_manifest_uri,
            },
        )


# ── private helpers ───────────────────────────────────────────────────────────


async def _call_inference_server(
    *,
    client: httpx.AsyncClient,
    endpoint_url: str,
    image_uri: str,
    box_threshold: float,
    text_threshold: float,
    max_image_size: int,
    detection_prompt: str | None,
) -> list[dict[str, Any]]:
    """POST /v1/detect with image_uri payload.

    The inference server resolves image_uri to actual image bytes.
    Workers do not read the image.
    """
    payload: dict[str, Any] = {
        "image_uri": image_uri,
        "box_threshold": box_threshold,
        "text_threshold": text_threshold,
        "max_image_size": max_image_size,
    }
    if detection_prompt is not None:
        payload["prompt"] = detection_prompt
    response = await client.post(f"{endpoint_url}/v1/detect", json=payload)
    response.raise_for_status()
    return response.json()["detections"]


def _build_predictions(
    *,
    sample: Any,
    detections_2d: list[dict[str, Any]],
    camera_sensor: Any,
    lidar_sensor: Any | None,
    raw_root: str,
    max_image_size: int,
) -> list[dict[str, Any]]:
    """Build per-prediction records from 2D detections + frustum lifting.

    NOTE: Frustum lifting currently fails with AttributeError because
    SceneSensorFrameManifest does not yet carry calibrated_sensor / ego_pose.
    When it fails, lifting_status="failed" is recorded; the prediction is kept
    with a [0,0,0] placeholder translation so downstream evaluation can filter
    it out via is_evaluable_prediction().
    TODO: Bridge SceneSensorFrameManifest calibration data to frustum_lift().
    """
    predictions: list[dict[str, Any]] = []
    for i, det in enumerate(detections_2d):
        bbox_2d: list[float] = det["bbox_2d"]
        category = det.get("category_name", "unknown")

        lift: dict[str, Any] | None = None
        lifting_status = "not_applicable"
        lifting_error: str | None = None

        if lidar_sensor is not None:
            try:
                lift = frustum_lift(
                    bbox_2d=bbox_2d,
                    camera_sensor=camera_sensor,
                    lidar_sensor=lidar_sensor,
                    raw_root=raw_root,
                    max_image_size=max_image_size,
                )
                lifting_status = "succeeded" if lift is not None else "not_applicable"
            except Exception as exc:
                lifting_status = "failed"
                lifting_error = str(exc)
                logger.warning(
                    "frustum_lift failed sample=%s idx=%d category=%s: %s",
                    sample.sample_id,
                    i,
                    category,
                    exc,
                )

        predictions.append(
            {
                "prediction_id": f"{sample.sample_id}-gdino-{i:04d}",
                "category_name": category,
                "translation": lift["translation"] if lift else [0.0, 0.0, 0.0],
                "size": lift["size"] if lift else [1.0, 1.0, 1.0],
                "rotation": lift["rotation"] if lift else [1.0, 0.0, 0.0, 0.0],
                "score": det["score"],
                "source_annotation_token": None,
                "bbox_2d": bbox_2d,
                "lifting_method": lift["lifting_method"] if lift else "none",
                "lifting_status": lifting_status,
                "lifting_error": lifting_error,
                "cluster_point_count": lift.get("cluster_point_count")
                if lift
                else None,
            }
        )
    return predictions


def _sample_prediction_manifest(
    *,
    run_id: str,
    dataset_manifest: DatasetManifest,
    model_id: str,
    model_version: str,
    sample_input: DetectionSampleInput,
    predictions: list[dict[str, Any]],
    endpoint_url: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "dataset_id": dataset_manifest.dataset_id,
        "dataset_version": dataset_manifest.dataset_version,
        "model_id": model_id,
        "model_version": model_version,
        "scene_id": sample_input.scene_id,
        "sample_id": sample_input.sample_id,
        "predictions": predictions,
        "metadata": {
            "backend": InferenceBackendType.GROUNDING_DINO.value,
            "camera_channel": sample_input.camera_channel,
            "image_uri": sample_input.image_uri,
            "endpoint_url": endpoint_url,
        },
    }
