from __future__ import annotations

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.config import ArtifactBackend, ArtifactSettings
from sceneops_storage.local import LocalArtifactStore


def create_artifact_store(settings: ArtifactSettings) -> ArtifactStore:
    if settings.backend == ArtifactBackend.LOCAL:
        return LocalArtifactStore(root_uri=settings.root_uri)

    raise NotImplementedError(f"Unsupported artifact backend: {settings.backend}")
