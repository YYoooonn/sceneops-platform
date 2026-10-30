from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from inference_server.image_resolver import IMAGE_CHECKSUM_PATTERN


class DetectRequest(BaseModel):
    # file:// or s3:// URI of the stored image object. The server reads it via
    # ImageResolver and decodes it only once its bytes match the pin below.
    image_uri: str
    # Pin of the whole stored object: "sha256:<64 lowercase hex>" and exact size.
    image_checksum: str = Field(pattern=IMAGE_CHECKSUM_PATTERN)
    image_size_bytes: int = Field(ge=0, strict=True)
    # None → use server-side defaults from InferenceServerSettings
    prompt: str | None = Field(default=None)
    box_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    text_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_image_size: int | None = Field(default=None, ge=64, le=2048)
    trace_id: str | None = Field(default=None)  # optional debug/logging field


class Detection2D(BaseModel):
    category_name: str
    score: float
    bbox_2d: list[float]  # [x1, y1, x2, y2] in pixels


class DetectResponse(BaseModel):
    detections: list[Detection2D]
    inference_ms: float
    device: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    model_loaded: bool
    warmup_enabled: bool = False
    warmup_completed: bool = False
    warmup_succeeded: bool | None = None
    warmup_elapsed_ms: float | None = None
    warmup_error: str | None = None
    max_concurrent_inference_requests: int | None = None
    active_inference_requests: int | None = None
    device: str | None = None
    model_id: str | None = None
    reason: str | None = None
