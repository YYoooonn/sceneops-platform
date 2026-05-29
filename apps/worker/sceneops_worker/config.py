from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import (
    ArtifactBackend,
    ArtifactSettings,
    DatasetArtifactSettings,
    DefaultDatasetSettings,
    RunArtifactSettings,
    WorkerRuntimeSettings,
)


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SCENEOPS_WORKER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://sceneops:sceneops@postgres:5432/sceneops"
    )

    artifact: ArtifactSettings = Field(
        default_factory=ArtifactSettings,
    )

    dataset_artifacts: DatasetArtifactSettings = Field(
        default_factory=DatasetArtifactSettings,
    )

    run_artifacts: RunArtifactSettings = Field(
        default_factory=RunArtifactSettings,
    )

    default_dataset: DefaultDatasetSettings = Field(
        default_factory=DefaultDatasetSettings,
    )

    runtime: WorkerRuntimeSettings = Field(
        default_factory=WorkerRuntimeSettings,
    )

    @property
    def artifact_backend(self) -> ArtifactBackend:
        return self.artifact.backend

    @property
    def artifact_root_uri(self) -> str:
        return self.artifact.root_uri

    @property
    def raw_data_root_uri(self) -> str | None:
        return self.dataset_artifacts.raw_data_root_uri

    @property
    def manifest_root_uri(self) -> str:
        return self.dataset_artifacts.manifest_root_uri

    @property
    def runs_root_uri(self) -> str:
        return self.run_artifacts.runs_root_uri

    @property
    def default_dataset_id(self) -> str:
        return self.default_dataset.dataset_id

    @property
    def default_dataset_version(self) -> str:
        return self.default_dataset.dataset_version

    @property
    def worker_id(self) -> str:
        return self.runtime.worker_id


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
