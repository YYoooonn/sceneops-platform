from __future__ import annotations

from pydantic import BaseModel, Field


class DetectRequest(BaseModel):
    image_path: str
    box_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    text_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    max_image_size: int = Field(default=800, ge=64, le=2048)


class Detection2D(BaseModel):
    category_name: str
    score: float
    bbox_2d: list[float]  # [x1, y1, x2, y2] in pixels


class DetectResponse(BaseModel):
    detections: list[Detection2D]
    inference_ms: float
    device: str
