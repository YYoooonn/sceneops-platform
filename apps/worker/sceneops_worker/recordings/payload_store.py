"""Write-once publication of canonical observation payloads (ADR-007 §30.5).

A payload is keyed by its deterministic artifact id under the RobotRun it
was extracted from::

    {payload_root}/{robot_run_id}/{artifact_id}

Both recording builders publish through this store, so a payload a Scene
build already extracted is reused by an Episode build of the same RobotRun
(and vice versa). Readers never derive the key: a manifest names a payload
by artifact id and its OBSERVATION_PAYLOAD ArtifactRecord holds the URI.
"""

from __future__ import annotations

from sceneops_core.common.checksums import sha256_checksum
from sceneops_storage import ArtifactStore


class ObservationPayloadConflictError(RuntimeError):
    """A write-once payload key already holds different bytes, or the bytes
    read back differ from the bytes written."""


class ObservationPayloadStore:
    def __init__(self, *, artifact_store: ArtifactStore, payload_root_uri: str) -> None:
        self.artifact_store = artifact_store
        self.payload_root_uri = payload_root_uri

    def payload_uri(self, *, robot_run_id: str, artifact_id: str) -> str:
        return self.artifact_store.join_uri(
            self.payload_root_uri, robot_run_id, artifact_id
        )

    async def publish(
        self, *, robot_run_id: str, artifact_id: str, data: bytes, checksum: str
    ) -> tuple[str, bool]:
        """Returns ``(uri, written)``. A key that already holds the same bytes
        is reused; different bytes are a conflict and never overwritten.
        ``checksum`` is the planned sha256 the bytes must have."""
        if sha256_checksum(data) != checksum:
            raise ObservationPayloadConflictError(
                f"payload {artifact_id} bytes do not match planned {checksum}"
            )
        uri = self.payload_uri(robot_run_id=robot_run_id, artifact_id=artifact_id)
        if await self.artifact_store.exists(uri):
            existing = await self.artifact_store.read_bytes(uri)
            if sha256_checksum(existing) != checksum:
                raise ObservationPayloadConflictError(
                    f"{uri} already holds different bytes; payload keys are write-once"
                )
            return uri, False
        await self.artifact_store.write_bytes(uri, data)
        if sha256_checksum(await self.artifact_store.read_bytes(uri)) != checksum:
            raise ObservationPayloadConflictError(f"read-back of {uri} differs")
        return uri, True


__all__ = ["ObservationPayloadConflictError", "ObservationPayloadStore"]
