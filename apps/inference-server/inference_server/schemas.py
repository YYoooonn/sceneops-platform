from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DetectRequest(BaseModel):
    # Primary: file:// URI (or future s3://, gs://).
    # The server resolves the URI to an image via ImageResolver.
    image_uri: str
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
