from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MetadataBackend(str, Enum):
    LOCAL_MANIFEST = "local_manifest"
    FIRESTORE = "firestore"


class StorageBackend(str, Enum):
    LOCAL = "local"
    GCS = "gcs"
    S3 = "s3"


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    metadata_backend: MetadataBackend = Field(
        default=MetadataBackend.LOCAL_MANIFEST,
        alias="METADATA_BACKEND",
    )
    storage_backend: StorageBackend = Field(
        default=StorageBackend.LOCAL,
        alias="STORAGE_BACKEND",
    )

    manifest_root: Path = Field(alias="MANIFEST_ROOT")
    raw_data_root: Path = Field(alias="RAW_DATA_ROOT")
    artifact_root: Path = Field(alias="ARTIFACT_ROOT")

    api_base_url: str = Field(
        default="http://localhost:8000",
        alias="API_BASE_URL",
    )

    gcs_bucket: str | None = Field(default=None, alias="GCS_BUCKET")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
