from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dataset_id: str = Field(default="nuscenes-mini", alias="DATASET_ID")
    dataset_version: str = Field(default="v1.0-mini", alias="DATASET_VERSION")

    nuscenes_root: Path = Field(alias="NUSCENES_ROOT")
    manifest_root: Path = Field(alias="MANIFEST_ROOT")
    artifact_root: Path | None = Field(default=None, alias="ARTIFACT_ROOT")


def get_settings() -> WorkerSettings:
    return WorkerSettings()
