from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sceneops_core.common.schemas import ArtifactUri


@runtime_checkable
class ArtifactStore(Protocol):
    """Port-like contract for artifact storage.

    Implementations may use local filesystem, MinIO, S3, GCS, or another
    object storage backend.

    The contract is async because object storage implementations are expected
    to be network-bound.
    """

    def join_uri(self, root: ArtifactUri, *parts: str) -> ArtifactUri:
        """Join URI parts while preserving the storage backend URI style."""

    async def exists(self, uri: ArtifactUri) -> bool:
        """Return whether the artifact exists."""

    async def read_json(self, uri: ArtifactUri) -> Any:
        """Read a JSON artifact."""

    async def write_json(
        self,
        uri: ArtifactUri,
        payload: Any,
    ) -> None:
        """Write a JSON artifact."""

    async def read_bytes(self, uri: ArtifactUri) -> bytes:
        """Read a binary artifact."""

    async def read_range(self, uri: ArtifactUri, offset: int, length: int) -> bytes:
        """Read exactly ``length`` bytes starting at byte ``offset`` (0-indexed
        from the start of the artifact) -- for selective Parquet reads
        (SceneOps V2 Request 5.3), not a general substitute for
        ``read_bytes``. ``offset >= 0`` and ``length > 0`` are the caller's
        responsibility; implementations raise ``ArtifactReadError`` for an
        invalid or out-of-bounds range and ``ArtifactNotFoundError`` if the
        artifact does not exist -- the same deterministic error contract on
        every backend, so callers never need backend-specific handling."""

    async def write_bytes(self, uri: ArtifactUri, data: bytes) -> None:
        """Write a binary artifact."""

    async def list_json(self, uri: ArtifactUri) -> list[ArtifactUri]:
        """List JSON artifact URIs under a prefix."""

    async def delete_prefix(self, uri: ArtifactUri) -> None:
        """Delete an artifact prefix or file."""

    def public_url(self, uri: ArtifactUri) -> str:
        """Return a public or displayable URL for the artifact."""
