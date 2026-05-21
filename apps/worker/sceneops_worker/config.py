from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    raw_data_root: Path = Field(alias="RAW_DATA_ROOT")
    manifest_root: Path = Field(alias="MANIFEST_ROOT")
    artifact_root: Path | None = Field(default=None, alias="ARTIFACT_ROOT")
    runs_root: Path = Field(alias="RUNS_ROOT")

    default_dataset_id: str = Field(default="nuscenes", alias="DEFAULT_DATASET_ID")
    default_dataset_version: str = Field(
        default="v1.0-mini",
        alias="DEFAULT_DATASET_VERSION",
    )


def get_settings() -> WorkerSettings:
    return WorkerSettings()
