from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from sceneops_core.inference.enums import InferenceBackendType
from sceneops_worker.inference.detection.base import (
    BackendRun,
    DetectionInferenceRequest,
    DetectionSampleInput,
    SampleDetectionBackend,
    SamplePrediction,
)
from sceneops_worker.inference.detection.frustum_lifting import frustum_lift
from sceneops_worker.inference.detection.pointcloud2 import decode_lidar_xyz

logger = logging.getLogger(__name__)

_DEFAULT_BOX_THRESHOLD = 0.35
_DEFAULT_TEXT_THRESHOLD = 0.25
_DEFAULT_MAX_IMAGE_SIZE = 800
_DEFAULT_HTTP_TIMEOUT = 120.0


class GroundingDinoDetectionBackend(SampleDetectionBackend):
    """HTTP client that delegates 2D detection to the GroundingDINO inference server.

    Sends image_uri (file:// or future remote URI) to the inference server,
    which resolves it and loads the image independently. Samples arrive
    already resolved from pinned sample views; this backend only issues HTTP
    requests, lifts boxes to 3-D from the sample's lidar observation and
    assembles prediction records.
    """

    def __init__(
        self,
        *,
        box_threshold: float = _DEFAULT_BOX_THRESHOLD,
        text_threshold: float = _DEFAULT_TEXT_THRESHOLD,
        max_image_size: int = _DEFAULT_MAX_IMAGE_SIZE,
        http_timeout: float = _DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self._box_threshold = box_threshold
        self._text_threshold = text_threshold
        self._max_image_size = max_image_size
        self._http_timeout = http_timeout

    @property
    def backend_type(self) -> str:
        return InferenceBackendType.GROUNDING_DINO.value

    async def predict(self, request: DetectionInferenceRequest) -> BackendRun:
        spec = request.input
        config = spec.config

        endpoint_url = (spec.endpoint_url or "").rstrip("/")
        if not endpoint_url:
            raise ValueError(
                "GroundingDINO backend requires endpoint_url pointing to the "
                "inference server (e.g. http://sceneops-inference:8001). "
                "Set it via the model version registry."
            )
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

        results: list[SamplePrediction] = []
        latencies_ms: list[float] = []
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            for sample in request.samples:
                started = time.perf_counter()
                detections_2d = await _call_inference_server(
                    client=client,
                    endpoint_url=endpoint_url,
                    image_uri=sample.image_uri,
                    box_threshold=box_threshold,
                    text_threshold=text_threshold,
                    max_image_size=max_image_size,
                    detection_prompt=config.detection_prompt,
                )
                latencies_ms.append((time.perf_counter() - started) * 1000.0)

                lidar_xyz, lidar_error = await _load_lidar(request, sample)
                results.append(
                    SamplePrediction(
                        sample=sample,
                        predictions=_build_predictions(
                            sample=sample,
                            detections_2d=detections_2d,
                            lidar_xyz=lidar_xyz,
                            lidar_error=lidar_error,
                            max_image_size=max_image_size,
                        ),
                        metadata={
                            "backend": self.backend_type,
                            "camera_channel": sample.camera_channel,
                            "image_uri": sample.image_uri,
                        },
                    )
                )

        avg_ms = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0
        return BackendRun(
            samples=results,
            inference_request_count=len(latencies_ms),
            metrics={
                "avg_roundtrip_ms": round(avg_ms, 2),
                "camera_channel": config.camera_channel,
                "lidar_channel": config.lidar_channel,
                "box_threshold": box_threshold,
                "text_threshold": text_threshold,
                "max_image_size": max_image_size,
            },
        )


# ── private helpers ───────────────────────────────────────────────────────────


async def _load_lidar(request: DetectionInferenceRequest, sample: DetectionSampleInput):
    """Decode the sample's lidar payload once, by its declared media type.
    Returns ``(xyz, error)``: exactly one is None; a sample without a lidar
    observation yields ``(None, None)``."""
    if sample.lidar is None or sample.lidar_uri is None:
        return None, None
    try:
        data = await request.artifact_store.read_bytes(sample.lidar_uri)
        return decode_lidar_xyz(sample.lidar.observation.payload.media_type, data), None
    except Exception as exc:  # decode / storage failure is recorded per sample
        logger.warning(
            "lidar payload of sample %s/%s is not decodable: %s",
            sample.scene_id,
            sample.sample_id,
            exc,
        )
        return None, str(exc)


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
    sample: DetectionSampleInput,
    detections_2d: list[dict[str, Any]],
    lidar_xyz,
    lidar_error: str | None = None,
    max_image_size: int,
) -> list[dict[str, Any]]:
    """Build per-prediction records from 2D detections + frustum lifting.

    ``lifting_status``: ``succeeded`` (3-D box lifted), ``not_applicable``
    (no lidar was requested for the sample, or the geometry lifting needs is
    absent) or ``failed`` (the lidar payload could not be decoded or lifting
    raised). Failed predictions are not evaluable."""
    predictions: list[dict[str, Any]] = []
    for i, det in enumerate(detections_2d):
        bbox_2d: list[float] = det["bbox_2d"]
        category = det.get("category_name", "unknown")

        lift: dict[str, Any] | None = None
        lifting_status = "not_applicable"
        lifting_error: str | None = None

        if sample.lidar is not None:
            if lidar_error is not None:
                lifting_status = "failed"
                lifting_error = lidar_error
            elif lidar_xyz is not None:
                try:
                    lift = frustum_lift(
                        bbox_2d=bbox_2d,
                        camera=sample.camera,
                        lidar=sample.lidar,
                        lidar_xyz=lidar_xyz,
                        pose=sample.pose,
                        max_image_size=max_image_size,
                    )
                    lifting_status = (
                        "succeeded" if lift is not None else "not_applicable"
                    )
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
                "frame_id": lift["frame_id"] if lift else None,
                "translation": lift["translation"] if lift else [0.0, 0.0, 0.0],
                "size": lift["size"] if lift else [1.0, 1.0, 1.0],
                "rotation": lift["rotation"] if lift else [1.0, 0.0, 0.0, 0.0],
                "score": det["score"],
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
