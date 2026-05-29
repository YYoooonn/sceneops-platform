from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ArtifactBackend(StrEnum):
    LOCAL = "local"
    MINIO = "minio"
    S3 = "s3"
    GCS = "gcs"


class ArtifactSettings(BaseModel):
    backend: ArtifactBackend = ArtifactBackend.LOCAL

    # local이면 /data, S3면 s3://bucket/prefix 같은 기준 root
    root_uri: str = "/data"

    # object storage 확장용
    bucket: str | None = None
    prefix: str | None = None
    endpoint_url: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


class DatasetArtifactSettings(BaseModel):
    # raw data는 dataset_versions.raw_data_uri가 source of truth.
    # 이 값은 개발 편의용 fallback으로만 사용.
    raw_data_root_uri: str | None = "/data/raw"

    manifest_root_uri: str = "/data/manifests"


class RunArtifactSettings(BaseModel):
    runs_root_uri: str = "/data/runs"


class DefaultDatasetSettings(BaseModel):
    dataset_id: str = "nuscenes"
    dataset_version: str = "v1.0-mini"


class WorkerRuntimeSettings(BaseModel):
    worker_id: str = "local-worker"
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 10.0
