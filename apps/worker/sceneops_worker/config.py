from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import (
    ArtifactSettings,
    ExecutionSettings,
    InputSourceSettings,
    WorkerRuntimeSettings,
)


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_prefix="SCENEOPS_WORKER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)
    input_source: InputSourceSettings = Field(default_factory=InputSourceSettings)
    runtime: WorkerRuntimeSettings = Field(
        default_factory=WorkerRuntimeSettings,
    )
    execution: ExecutionSettings = Field(
        default_factory=ExecutionSettings,
    )

    @property
    def artifact_root_uri(self) -> str:
        return self.artifact.root_uri

    @property
    def dataset_root_uri(self) -> str:
        return self.artifact.dataset_root_uri

    @property
    def run_root_uri(self) -> str:
        return self.artifact.run_root_uri

    @property
    def observation_payload_root_uri(self) -> str:
        return self.artifact.observation_payload_root_uri

    @property
    def label_root_uri(self) -> str:
        return self.artifact.label_root_uri

    @property
    def analytics_root_uri(self) -> str:
        return self.artifact.analytics_root_uri

    @property
    def worker_id(self) -> str:
        return self.runtime.worker_id


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
