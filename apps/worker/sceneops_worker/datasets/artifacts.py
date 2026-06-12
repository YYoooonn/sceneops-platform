from __future__ import annotations

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_storage import ArtifactStore


class DatasetArtifactStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        dataset_root_uri: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.dataset_root_uri = dataset_root_uri

    # ------------------------------------------------------------------
    # URI helpers
    # ------------------------------------------------------------------

    def dataset_version_root_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.dataset_root_uri,
            dataset_id,
            "versions",
            dataset_version,
        )

    def dataset_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> str:
        version_root = self.dataset_version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(version_root, "dataset.json")

    # ------------------------------------------------------------------
    # Dataset manifest I/O
    # ------------------------------------------------------------------

    async def write_dataset_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        manifest: DatasetManifest,
    ) -> str:
        uri = self.dataset_manifest_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())
        return uri

    async def read_dataset_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> DatasetManifest | None:
        uri = self.dataset_manifest_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        if not await self.artifact_store.exists(uri):
            return None
        raw = await self.artifact_store.read_json(uri)
        return DatasetManifest.model_validate(raw)

    async def load_dataset_manifest(self, uri: str) -> DatasetManifest:
        raw = await self.artifact_store.read_json(uri)
        return DatasetManifest.model_validate(raw)

    async def save_dataset_manifest(
        self,
        *,
        uri: str,
        manifest: DatasetManifest,
    ) -> None:
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())

    async def reset_dataset_version(self, version_root_uri: str) -> None:
        await self.artifact_store.delete_prefix(version_root_uri)
