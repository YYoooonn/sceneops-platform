from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class InferenceServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],  # .env.local takes precedence
        env_prefix="SCENEOPS_INFERENCE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8001)

    # Model
    model_id: str = Field(default="IDEA-Research/grounding-dino-tiny")
    hf_cache_dir: str | None = Field(default=None)

    # Detection defaults (can be overridden per-request via DetectRequest)
    detection_prompt: str = Field(default="car . person . barrier .")
    box_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    text_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    max_image_size: int = Field(default=800, ge=64, le=2048)

    # Warmup
    enable_warmup: bool = Field(default=True)
    # If True, /readyz returns 503 when warmup has failed.
    require_warmup_success_for_ready: bool = Field(default=False)
    warmup_image_size: int = Field(default=256, ge=64, le=2048)
    warmup_text_prompt: str = Field(default="car . person . barrier .")

    # Concurrency
    max_concurrent_inference_requests: int = Field(default=1, ge=1)

    # Image URI security: file:// paths must be under one of these roots.
    allowed_file_roots: list[str] = Field(
        default_factory=lambda: ["/data/raw", "/data/artifacts"]
    )

    @field_validator("allowed_file_roots", mode="before")
    @classmethod
    def _parse_csv_roots(cls, v: object) -> object:
        if isinstance(v, str):
            return [r.strip() for r in v.split(",") if r.strip()]
        return v


@lru_cache
def get_settings() -> InferenceServerSettings:
    return InferenceServerSettings()
