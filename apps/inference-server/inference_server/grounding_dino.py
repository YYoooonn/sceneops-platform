from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from inference_server.config import InferenceServerSettings, get_settings
from inference_server.schemas import DetectRequest, Detection2D

# Maps GroundingDINO output phrase → nuScenes category name.
# Extend this dict to support more categories without changing other code.
LABEL_TO_CATEGORY: dict[str, str] = {
    "car": "vehicle.car",
    "person": "human.pedestrian.adult",
    "barrier": "movable_object.barrier",
}


class GroundingDinoModel:
    """GroundingDINO-T wrapper. Load once at server startup, reuse per request."""

    def __init__(self, settings: InferenceServerSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._processor: Any = None
        self._model: Any = None
        self._device: str = "cpu"

    def load(self) -> None:
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

        if torch.cuda.is_available():
            self._device = "cuda"
        elif torch.backends.mps.is_available():
            self._device = "mps"
        else:
            self._device = "cpu"

        cache_dir = self._settings.hf_cache_dir
        self._processor = AutoProcessor.from_pretrained(
            self._settings.model_id,
            cache_dir=cache_dir,
        )
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            self._settings.model_id,
            cache_dir=cache_dir,
        ).to(self._device)
        self._model.eval()

    @property
    def device(self) -> str:
        return self._device

    def detect(self, request: DetectRequest) -> tuple[list[Detection2D], float]:
        """Run GroundingDINO on a single image.

        Returns (detections, inference_ms). Runs synchronously — call via
        asyncio.to_thread from async handlers.

        TODO: accept MinIO/S3 image URIs for production deployments where
        the GPU server cannot share a local volume with the worker.
        """
        import torch
        from PIL import Image

        image_path = Path(request.image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        image = Image.open(image_path).convert("RGB")
        image = _resize_long_edge(image, request.max_image_size)
        img_w, img_h = image.size

        prompt = request.prompt or self._settings.detection_prompt
        inputs = self._processor(
            images=image,
            text=prompt,
            return_tensors="pt",
        ).to(self._device)

        t0 = time.perf_counter()
        with torch.no_grad():
            outputs = self._model(**inputs)
        inference_ms = (time.perf_counter() - t0) * 1000.0

        # transformers >= 4.51 uses "text_labels" (str) instead of "labels" (int).
        results = self._processor.post_process_grounded_object_detection(
            outputs,
            threshold=request.box_threshold,
            text_threshold=request.text_threshold,
            target_sizes=[(img_h, img_w)],
        )[0]

        label_key = "text_labels" if "text_labels" in results else "labels"
        detections: list[Detection2D] = []
        for box, label, score in zip(
            results["boxes"].tolist(),
            results[label_key],
            results["scores"].tolist(),
        ):
            category = LABEL_TO_CATEGORY.get(label.strip().lower())
            if category is None:
                continue
            x1, y1, x2, y2 = box
            detections.append(
                Detection2D(
                    category_name=category,
                    score=round(float(score), 4),
                    bbox_2d=[round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                )
            )

        return detections, inference_ms


def _resize_long_edge(image: Any, max_size: int) -> Any:
    from PIL import Image

    w, h = image.size
    scale = min(max_size / max(w, h), 1.0)
    if scale >= 1.0:
        return image
    return image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
