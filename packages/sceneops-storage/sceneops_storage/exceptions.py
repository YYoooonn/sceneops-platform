from __future__ import annotations


class ArtifactStoreError(Exception):
    """Base exception for artifact store operations."""


class ArtifactNotFoundError(ArtifactStoreError):
    """Raised when an artifact does not exist at the given URI."""

    def __init__(self, uri: str) -> None:
        super().__init__(f"Artifact not found: {uri}")
        self.uri = uri


class ArtifactReadError(ArtifactStoreError):
    """Raised when reading an artifact fails."""


class ArtifactWriteError(ArtifactStoreError):
    """Raised when writing an artifact fails."""
