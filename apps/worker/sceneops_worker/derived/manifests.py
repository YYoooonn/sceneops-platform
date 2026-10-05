"""Write-once, checksum-pinned storage of derived manifests (ADR-007 §33.1).

Label sets, sample views, ScenarioSets and prediction manifests are
immutable revisions. Each is stored at a key qualified by its own checksum,
so revisions coexist and no key is ever overwritten, and every read verifies
the pinned checksum before the bytes are parsed::

    labels        {label_root}/{label_set_id}/manifest-{hex}.json
    sample view   {dataset_root}/{dataset_id}/versions/{version}/scenes/{scene_id}/sample_views/manifest-{hex}.json
    scenario set  {dataset_root}/{dataset_id}/versions/{version}/scenario_sets/{scenario_set_id}/manifest-{hex}.json
    predictions   {runs_root}/inference/{run_id}/prediction_manifest-{hex}.json

Retries converge: publishing the same bytes again is a no-op, and different
bytes under a key that already exists fail loudly instead of overwriting.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.common.checksums import checksum_hex, sha256_checksum
from sceneops_core.inference.schemas.manifests import (
    DetectionPredictionManifest,
    load_canonical_prediction_manifest,
)
from sceneops_core.labels.schemas import LabelSetManifest, load_canonical_label_set
from sceneops_core.sample_views.schemas import (
    SceneSampleViewManifest,
    load_canonical_sample_view,
)
from sceneops_core.scenarios.schemas.manifests import (
    ScenarioSetManifest,
    load_canonical_scenario_set,
)
from sceneops_storage import ArtifactNotFoundError, ArtifactStore


class DerivedManifestIntegrityError(RuntimeError):
    """Stored manifest bytes are missing or do not match their pinned
    checksum."""


class DerivedManifestConflictError(RuntimeError):
    """A write-once manifest key already holds different bytes."""


@dataclass(frozen=True)
class PublishedManifest:
    uri: str
    checksum: str
    size_bytes: int
    created: bool


class DerivedManifestStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        dataset_root_uri: str,
        runs_root_uri: str,
        label_root_uri: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.dataset_root_uri = dataset_root_uri
        self.runs_root_uri = runs_root_uri
        self.label_root_uri = label_root_uri

    # ------------------------------------------------------------------
    # keys
    # ------------------------------------------------------------------

    def label_set_uri(self, *, label_set_id: str, checksum: str) -> str:
        return self.artifact_store.join_uri(
            self.label_root_uri, label_set_id, f"manifest-{checksum_hex(checksum)}.json"
        )

    def _version_root(self, dataset_id: str, dataset_version: str) -> str:
        return self.artifact_store.join_uri(
            self.dataset_root_uri, dataset_id, "versions", dataset_version
        )

    def sample_view_uri(
        self, *, dataset_id: str, dataset_version: str, scene_id: str, checksum: str
    ) -> str:
        return self.artifact_store.join_uri(
            self._version_root(dataset_id, dataset_version),
            "scenes",
            scene_id,
            "sample_views",
            f"manifest-{checksum_hex(checksum)}.json",
        )

    def scenario_set_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        scenario_set_id: str,
        checksum: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self._version_root(dataset_id, dataset_version),
            "scenario_sets",
            scenario_set_id,
            f"manifest-{checksum_hex(checksum)}.json",
        )

    def prediction_manifest_uri(self, *, inference_run_id: str, checksum: str) -> str:
        return self.artifact_store.join_uri(
            self.runs_root_uri,
            "inference",
            inference_run_id,
            f"prediction_manifest-{checksum_hex(checksum)}.json",
        )

    # ------------------------------------------------------------------
    # write-once publication and pinned reads
    # ------------------------------------------------------------------

    async def publish(self, *, uri: str, data: bytes) -> PublishedManifest:
        checksum = sha256_checksum(data)
        if await self.artifact_store.exists(uri):
            if await self.artifact_store.read_bytes(uri) != data:
                raise DerivedManifestConflictError(
                    f"{uri} already holds different bytes; manifest keys are write-once"
                )
            return PublishedManifest(uri, checksum, len(data), created=False)
        await self.artifact_store.write_bytes(uri, data)
        if await self.artifact_store.read_bytes(uri) != data:
            raise DerivedManifestIntegrityError(f"read-back of {uri} differs")
        return PublishedManifest(uri, checksum, len(data), created=True)

    async def read_pinned(
        self, *, uri: str, checksum: str, size_bytes: int | None = None
    ) -> bytes:
        try:
            data = await self.artifact_store.read_bytes(uri)
        except (ArtifactNotFoundError, FileNotFoundError) as exc:
            raise DerivedManifestIntegrityError(f"manifest not found: {uri}") from exc
        if size_bytes is not None and len(data) != size_bytes:
            raise DerivedManifestIntegrityError(
                f"manifest {uri} has {len(data)} bytes, pinned {size_bytes}"
            )
        actual = sha256_checksum(data)
        if actual != checksum:
            raise DerivedManifestIntegrityError(
                f"manifest {uri} checksum {actual} != pinned {checksum}"
            )
        return data

    async def read_label_set(
        self, *, uri: str, checksum: str, size_bytes: int | None = None
    ) -> LabelSetManifest:
        return load_canonical_label_set(
            await self.read_pinned(uri=uri, checksum=checksum, size_bytes=size_bytes)
        )

    async def read_sample_view(
        self, *, uri: str, checksum: str, size_bytes: int | None = None
    ) -> SceneSampleViewManifest:
        return load_canonical_sample_view(
            await self.read_pinned(uri=uri, checksum=checksum, size_bytes=size_bytes)
        )

    async def read_scenario_set(
        self, *, uri: str, checksum: str, size_bytes: int | None = None
    ) -> ScenarioSetManifest:
        return load_canonical_scenario_set(
            await self.read_pinned(uri=uri, checksum=checksum, size_bytes=size_bytes)
        )

    async def read_prediction_manifest(
        self, *, uri: str, checksum: str, size_bytes: int | None = None
    ) -> DetectionPredictionManifest:
        return load_canonical_prediction_manifest(
            await self.read_pinned(uri=uri, checksum=checksum, size_bytes=size_bytes)
        )


__all__ = [
    "DerivedManifestConflictError",
    "DerivedManifestIntegrityError",
    "DerivedManifestStore",
    "PublishedManifest",
]
