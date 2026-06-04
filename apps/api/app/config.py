from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import (
    ArtifactSettings,
    DefaultDatasetSettings,
    ExecutionSettings,
)


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SCENEOPS_API_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    default_dataset: DefaultDatasetSettings = Field(
        default_factory=DefaultDatasetSettings,
    )

    @property
    def default_dataset_id(self) -> str:
        return self.default_dataset.dataset_id

    @property
    def default_dataset_version(self) -> str:
        return self.default_dataset.dataset_version


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
