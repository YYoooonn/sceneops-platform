from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ArtifactBackend(StrEnum):
    LOCAL = "local"
    MINIO = "minio"
    S3 = "s3"
    GCS = "gcs"


class ArtifactSettings(BaseModel):
    backend: ArtifactBackend = ArtifactBackend.LOCAL

    # Local: /data or file:///data
    # Object storage: s3://bucket/prefix, gs://bucket/prefix
    root_uri: str = "/data"

    dataset_prefix: str = "datasets"
    run_prefix: str = "runs"
    model_prefix: str = "models"

    # Object storage extension fields.
    bucket: str | None = None
    prefix: str | None = None
    endpoint_url: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None

    @property
    def dataset_root_uri(self) -> str:
        return join_uri(self.root_uri, self.dataset_prefix)

    @property
    def run_root_uri(self) -> str:
        return join_uri(self.root_uri, self.run_prefix)

    @property
    def model_root_uri(self) -> str:
        return join_uri(self.root_uri, self.model_prefix)


class DefaultDatasetSettings(BaseModel):
    dataset_id: str = "nuscenes"
    dataset_version: str = "v1.0-mini"


class WorkerRuntimeSettings(BaseModel):
    worker_id: str = "local-worker"
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 10.0


def join_uri(root: str, *parts: str) -> str:
    normalized_root = root.rstrip("/")
    normalized_parts = [part.strip("/") for part in parts if part.strip("/")]
    if not normalized_parts:
        return normalized_root
    return "/".join([normalized_root, *normalized_parts])
