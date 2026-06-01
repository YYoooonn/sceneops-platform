from __future__ import annotations

from typing import Protocol, runtime_checkable

from sceneops_core.common.types import ArtifactUri, JsonDict


@runtime_checkable
class ArtifactStore(Protocol):
    """Port-like contract for artifact storage.

    Implementations may use local filesystem, MinIO, S3, GCS, or another
    object storage backend.
    """

    def put_json(
        self,
        uri: ArtifactUri,
        data: JsonDict,
        *,
        artifact_type: str,
    ) -> ArtifactUri:
        """Store a JSON artifact and return its persisted URI."""

    def get_json(self, uri: ArtifactUri) -> JsonDict:
        """Load a JSON artifact from storage."""

    def exists(self, uri: ArtifactUri) -> bool:
        """Return whether the artifact exists."""

    def resolve_uri(self, uri: ArtifactUri) -> str:
        """Resolve a logical URI into a backend-specific path or URL."""
