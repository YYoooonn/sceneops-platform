from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import (
    ArtifactSettings,
    ExecutionSettings,
)


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_prefix="SCENEOPS_API_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
