from __future__ import annotations

from collections.abc import Callable

from sceneops_core.artifacts import ArtifactBackend, ArtifactStore
from sceneops_core.config import ArtifactSettings

from sceneops_storage.backends.local import LocalArtifactStore
from sceneops_storage.backends.s3 import S3ArtifactStore

_REGISTRY: dict[ArtifactBackend, Callable[[ArtifactSettings], ArtifactStore]] = {
    ArtifactBackend.LOCAL: lambda s: LocalArtifactStore(root_uri=s.root_uri),
    ArtifactBackend.S3: lambda s: S3ArtifactStore(settings=s),
    ArtifactBackend.MINIO: lambda s: S3ArtifactStore(settings=s),
}


def create_artifact_store(settings: ArtifactSettings) -> ArtifactStore:
    factory = _REGISTRY.get(settings.backend)
    if factory is None:
        raise NotImplementedError(f"Unsupported artifact backend: {settings.backend}")
    return factory(settings)
