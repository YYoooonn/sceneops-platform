"""Episode artifact storage.

Canonical EpisodeManifest revisions live at write-once, checksum-qualified
keys (ADR-007 §19)::

    {dataset_root}/{dataset_id}/versions/{version}/episodes/{episode_id}/manifest-{sha256}.json

so every revision coexists and no key is ever overwritten. Reading a
canonical manifest always goes through a pinned checksum: the bytes are
verified before they are parsed, and parsing requires canonical form.
Observation payloads go through the shared :class:`ObservationPayloadStore`.
Derived aligned-episode artifacts and their reports live next to the
canonical revisions, under ``episodes/{episode_id}/aligned/``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sceneops_core.common.checksums import (
    checksum_qualified_manifest_name,
    sha256_checksum,
)
from sceneops_core.common.schemas import SceneOpsBaseModel
from sceneops_core.episodes.alignment import AlignedEpisodeArtifact
from sceneops_core.episodes.schemas import (
    EpisodeManifest,
    load_canonical_episode_manifest,
)
from sceneops_storage import ArtifactNotFoundError, ArtifactStore

from sceneops_worker.recordings.payload_store import ObservationPayloadStore


class EpisodeManifestIntegrityError(RuntimeError):
    """Stored manifest bytes are missing or do not match their pinned
    checksum / size."""


class EpisodeManifestWriteConflictError(RuntimeError):
    """A write-once manifest key already holds different bytes."""


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic, compact JSON encoding used for every Episode-domain
    artifact this store writes -- the exact bytes returned here are the
    exact bytes written to ArtifactStore and the exact bytes a checksum is
    computed over (SceneOps V2 Request 2.3 §3): writing via write_bytes with
    a self-controlled serialization, rather than write_json's own internal
    serialization, removes any risk of a mismatch between "bytes hashed" and
    "bytes written". Sorted keys + no indentation is a deliberate departure
    from the platform's general write_json(indent=2) convention, scoped to
    this store only, in exchange for exact-checksum determinism."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class PublishedEpisodeManifest:
    uri: str
    checksum: str
    size_bytes: int


@dataclass(frozen=True)
class EpisodeArtifactWriteResult:
    uri: str
    checksum: str
    size_bytes: int


class EpisodeArtifactStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        dataset_root_uri: str,
        payload_root_uri: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.dataset_root_uri = dataset_root_uri
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

    def canonical_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
        checksum: str,
    ) -> str:
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(
            version_root,
            "episodes",
            episode_id,
            checksum_qualified_manifest_name(checksum),
        )

    def aligned_episode_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
        source_manifest_sha256: str,
        alignment_key: str,
    ) -> str:
        """datasets/{dataset_id}/versions/{dataset_version}/episodes/{episode_id}/aligned/{source_hash}/{alignment_key}.json
        (SceneOps V2 Request 2.3 §14).

        Two independent, truncated (64-bit, collision-negligible at this
        scale) hash segments rather than one combined identity hash --
        source_manifest_sha256[:16] answers "which source content", the
        alignment_key[:16] segment (config + semantics version, SceneOps V2
        Request 2.3 persistence.alignment_key) answers "which alignment
        recipe", independently of each other. A human reading the path can
        tell which one changed between two runs; a single opaque combined
        hash could not.
        """
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(
            version_root,
            "episodes",
            episode_id,
            "aligned",
            source_manifest_sha256[:16],
            f"{alignment_key[:16]}.json",
        )

    # ------------------------------------------------------------------
    # Canonical EpisodeManifest I/O
    # ------------------------------------------------------------------

    async def publish_canonical_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
        manifest: EpisodeManifest,
    ) -> PublishedEpisodeManifest:
        """Write-once publication at the checksum-qualified key. Re-publishing
        identical bytes is a no-op; a key that already holds different bytes
        is a conflict and is never overwritten."""
        data = manifest.to_canonical_bytes()
        checksum = sha256_checksum(data)
        uri = self.canonical_manifest_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            episode_id=episode_id,
            checksum=checksum,
        )
        if await self.artifact_store.exists(uri):
            if await self.artifact_store.read_bytes(uri) != data:
                raise EpisodeManifestWriteConflictError(
                    f"{uri} already holds different bytes; manifest keys are write-once"
                )
        else:
            await self.artifact_store.write_bytes(uri, data)
            if await self.artifact_store.read_bytes(uri) != data:
                raise EpisodeManifestIntegrityError(f"read-back of {uri} differs")
        return PublishedEpisodeManifest(
            uri=uri, checksum=checksum, size_bytes=len(data)
        )

    async def read_pinned_manifest_bytes(
        self, *, uri: str, checksum: str, size_bytes: int | None = None
    ) -> bytes:
        """The exact bytes of a pinned revision, verified before any parse."""
        try:
            data = await self.artifact_store.read_bytes(uri)
        except (ArtifactNotFoundError, FileNotFoundError) as exc:
            raise EpisodeManifestIntegrityError(
                f"episode manifest not found: {uri}"
            ) from exc
        if size_bytes is not None and len(data) != size_bytes:
            raise EpisodeManifestIntegrityError(
                f"episode manifest {uri} has {len(data)} bytes, pinned {size_bytes}"
            )
        actual = sha256_checksum(data)
        if actual != checksum:
            raise EpisodeManifestIntegrityError(
                f"episode manifest {uri} checksum {actual} != pinned {checksum}"
            )
        return data

    async def read_pinned_manifest(
        self, *, uri: str, checksum: str, size_bytes: int | None = None
    ) -> EpisodeManifest:
        data = await self.read_pinned_manifest_bytes(
            uri=uri, checksum=checksum, size_bytes=size_bytes
        )
        return load_canonical_episode_manifest(data)

    # ------------------------------------------------------------------
    # Aligned episode artifact I/O (SceneOps V2 Request 2.3)
    # ------------------------------------------------------------------

    async def write_aligned_episode(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
        source_manifest_sha256: str,
        alignment_key: str,
        artifact: AlignedEpisodeArtifact,
    ) -> EpisodeArtifactWriteResult:
        uri = self.aligned_episode_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            episode_id=episode_id,
            source_manifest_sha256=source_manifest_sha256,
            alignment_key=alignment_key,
        )
        data = _canonical_bytes(artifact.to_artifact_dict())
        # An AlignedEpisode is a pure function of (source revision, alignment
        # recipe), and both are in the key, so the same key always holds the
        # same bytes: a retry is a no-op, anything else is a conflict.
        if await self.artifact_store.exists(uri):
            if await self.artifact_store.read_bytes(uri) != data:
                raise EpisodeManifestWriteConflictError(
                    f"{uri} already holds a different aligned episode; aligned "
                    "episode keys are write-once"
                )
        else:
            await self.artifact_store.write_bytes(uri, data)
        return EpisodeArtifactWriteResult(
            uri=uri, checksum=f"sha256:{_sha256_hex(data)}", size_bytes=len(data)
        )

    async def read_aligned_episode_bytes(self, uri: str) -> bytes | None:
        """Raw bytes, for callers (VALIDATE_ALIGNED_EPISODE/
        PROFILE_ALIGNED_EPISODE) that need to hash the exact aligned-artifact
        content before parsing it -- SceneOps V2 Request 2.4 §37, mirroring
        read_episode_manifest_bytes's role for Request 2.3."""
        if not await self.artifact_store.exists(uri):
            return None
        return await self.artifact_store.read_bytes(uri)

    # ------------------------------------------------------------------
    # Aligned episode analysis report I/O (SceneOps V2 Request 2.4)
    # ------------------------------------------------------------------

    def aligned_episode_report_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
        source_manifest_sha256: str,
        alignment_key: str,
        report_kind: str,
    ) -> str:
        """Sibling of the aligned artifact's own URI -- same two identity
        segments (source hash, alignment key), suffixed by report_kind
        ("validation" | "profile") rather than nested under the aligned
        artifact's own ``.json`` file, so a listing of the ``aligned/{hash}/``
        prefix shows the artifact and its analyses grouped together by name
        (SceneOps V2 Request 2.4 §30)."""
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(
            version_root,
            "episodes",
            episode_id,
            "aligned",
            source_manifest_sha256[:16],
            f"{alignment_key[:16]}.{report_kind}.json",
        )

    async def write_aligned_episode_report(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
        source_manifest_sha256: str,
        alignment_key: str,
        report_kind: str,
        report: SceneOpsBaseModel,
    ) -> EpisodeArtifactWriteResult:
        uri = self.aligned_episode_report_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            episode_id=episode_id,
            source_manifest_sha256=source_manifest_sha256,
            alignment_key=alignment_key,
            report_kind=report_kind,
        )
        data = _canonical_bytes(report.to_artifact_dict())
        await self.artifact_store.write_bytes(uri, data)
        return EpisodeArtifactWriteResult(
            uri=uri, checksum=f"sha256:{_sha256_hex(data)}", size_bytes=len(data)
        )
