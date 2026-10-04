"""Scene artifact storage.

Canonical SceneManifest revisions live at write-once, checksum-qualified
keys (ADR-007 §19)::

    {dataset_root}/{dataset_id}/versions/{version}/scenes/{scene_id}/manifest-{sha256}.json

so every revision of a Scene coexists and no key is ever overwritten.
Reading a canonical manifest always goes through a pinned checksum: the
bytes are verified before they are parsed, and parsing requires canonical
form.

Canonical observation payloads are write-once objects keyed by their
deterministic artifact id under the RobotRun they were extracted from::

    {payload_root}/{robot_run_id}/{artifact_id}

Readers never derive this key: a manifest names a payload by artifact id
and its OBSERVATION_PAYLOAD ArtifactRecord holds the URI.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.common.checksums import (
    checksum_qualified_manifest_name,
    sha256_checksum,
)
from sceneops_core.datasets.schemas import DatasetSceneIndexEntry
from sceneops_core.scenes.schemas import SceneManifest, load_canonical_scene_manifest
from sceneops_storage import ArtifactNotFoundError, ArtifactStore

from sceneops_worker.recordings.payload_store import (
    ObservationPayloadConflictError,
    ObservationPayloadStore,
)


class SceneManifestIntegrityError(RuntimeError):
    """Stored manifest bytes are missing or do not match their pinned
    checksum / size."""


class SceneManifestWriteConflictError(RuntimeError):
    """A write-once manifest key already holds different bytes."""


@dataclass(frozen=True)
class PublishedSceneManifest:
    uri: str
    checksum: str
    size_bytes: int


class SceneArtifactStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        dataset_root_uri: str,
        payload_root_uri: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.dataset_root_uri = dataset_root_uri
        self.payload_root_uri = payload_root_uri
        self.payload_store = ObservationPayloadStore(
            artifact_store=artifact_store, payload_root_uri=payload_root_uri
        )

    # ------------------------------------------------------------------
    # URI helpers
    # ------------------------------------------------------------------

    def _version_root_uri(self, *, dataset_id: str, dataset_version: str) -> str:
        return self.artifact_store.join_uri(
            self.dataset_root_uri,
            dataset_id,
            "versions",
            dataset_version,
        )

    def scenes_root_uri(self, *, dataset_id: str, dataset_version: str) -> str:
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(version_root, "scenes")

    def canonical_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
        checksum: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.scenes_root_uri(
                dataset_id=dataset_id, dataset_version=dataset_version
            ),
            scene_id,
            checksum_qualified_manifest_name(checksum),
        )

    def observation_payload_uri(self, *, robot_run_id: str, artifact_id: str) -> str:
        return self.payload_store.payload_uri(
            robot_run_id=robot_run_id, artifact_id=artifact_id
        )

    def scene_index_uri(self, *, dataset_id: str, dataset_version: str) -> str:
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(version_root, "scene_index.json")

    # ------------------------------------------------------------------
    # Canonical SceneManifest I/O
    # ------------------------------------------------------------------

    async def publish_canonical_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
        manifest: SceneManifest,
    ) -> PublishedSceneManifest:
        """Write-once publication at the checksum-qualified key. Re-publishing
        identical bytes is a no-op; a key that already holds different bytes
        is a conflict and is never overwritten."""
        data = manifest.to_canonical_bytes()
        checksum = sha256_checksum(data)
        uri = self.canonical_manifest_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
            checksum=checksum,
        )
        if await self.artifact_store.exists(uri):
            if await self.artifact_store.read_bytes(uri) != data:
                raise SceneManifestWriteConflictError(
                    f"{uri} already holds different bytes; manifest keys are write-once"
                )
        else:
            await self.artifact_store.write_bytes(uri, data)
            if await self.artifact_store.read_bytes(uri) != data:
                raise SceneManifestIntegrityError(f"read-back of {uri} differs")
        return PublishedSceneManifest(uri=uri, checksum=checksum, size_bytes=len(data))

    async def read_pinned_manifest(
        self,
        *,
        uri: str,
        checksum: str,
        size_bytes: int | None = None,
    ) -> SceneManifest:
        """Read a canonical manifest revision and verify it is exactly the
        pinned bytes before parsing it strictly."""
        try:
            data = await self.artifact_store.read_bytes(uri)
        except (ArtifactNotFoundError, FileNotFoundError) as exc:
            raise SceneManifestIntegrityError(
                f"scene manifest not found: {uri}"
            ) from exc
        if size_bytes is not None and len(data) != size_bytes:
            raise SceneManifestIntegrityError(
                f"scene manifest {uri} has {len(data)} bytes, pinned {size_bytes}"
            )
        actual = sha256_checksum(data)
        if actual != checksum:
            raise SceneManifestIntegrityError(
                f"scene manifest {uri} checksum {actual} != pinned {checksum}"
            )
        return load_canonical_scene_manifest(data)

    # ------------------------------------------------------------------
    # Canonical observation payloads
    # ------------------------------------------------------------------

    async def publish_observation_payload(
        self, *, robot_run_id: str, artifact_id: str, data: bytes, checksum: str
    ) -> tuple[str, bool]:
        """Write-once; see :class:`ObservationPayloadStore`."""
        return await self.payload_store.publish(
            robot_run_id=robot_run_id,
            artifact_id=artifact_id,
            data=data,
            checksum=checksum,
        )

    # ------------------------------------------------------------------
    # Derived scene index
    # ------------------------------------------------------------------

    async def write_scene_index(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        entries: list[DatasetSceneIndexEntry],
    ) -> str:
        uri = self.scene_index_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        payload = {
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "scene_count": len(entries),
            "scenes": [e.model_dump(mode="json") for e in entries],
        }
        await self.artifact_store.write_json(uri, payload)
        return uri


__all__ = [
    "ObservationPayloadConflictError",
    "PublishedSceneManifest",
    "SceneArtifactStore",
    "SceneManifestIntegrityError",
    "SceneManifestWriteConflictError",
]
