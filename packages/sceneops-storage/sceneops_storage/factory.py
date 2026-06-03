from __future__ import annotations

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.config import ArtifactBackend, ArtifactSettings
from sceneops_storage.local import LocalArtifactStore
from sceneops_storage.s3 import S3ArtifactStore


def create_artifact_store(settings: ArtifactSettings) -> ArtifactStore:
    if settings.backend == ArtifactBackend.LOCAL:
        return LocalArtifactStore(root_uri=settings.root_uri)

    # MinIO is S3-compatible; both are served by the same S3 store, with MinIO
    # selected by pointing endpoint_url at the MinIO service.
    if settings.backend in (ArtifactBackend.S3, ArtifactBackend.MINIO):
        return S3ArtifactStore(settings=settings)

    raise NotImplementedError(f"Unsupported artifact backend: {settings.backend}")
