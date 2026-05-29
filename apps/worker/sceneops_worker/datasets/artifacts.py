from __future__ import annotations

from collections.abc import AsyncIterator

from sceneops_core.schemas.datasets import (
    DatasetManifest,
    DatasetSampleManifest,
    DatasetSceneIndex,
    DatasetSceneManifest,
)
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

    def dataset_manifest_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(version_root_uri, "dataset.json")

    def scene_index_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(version_root_uri, "scenes.json")

    def scene_root_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(version_root_uri, "scenes")

    def sample_root_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(version_root_uri, "samples")

    def scene_manifest_uri(
        self,
        *,
        version_root_uri: str,
        scene_id: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.scene_root_uri(version_root_uri),
            f"{scene_id}.json",
        )

    def sample_manifest_uri(
        self,
        *,
        version_root_uri: str,
        sample_id: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.sample_root_uri(version_root_uri),
            f"{sample_id}.json",
        )

    async def reset_dataset_version(self, version_root_uri: str) -> None:
        await self.artifact_store.delete_prefix(version_root_uri)

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

    async def load_scene_index(self, uri: str) -> DatasetSceneIndex | None:
        if not await self.artifact_store.exists(uri):
            return None
        raw = await self.artifact_store.read_json(uri)
        return DatasetSceneIndex.model_validate(raw)

    async def save_scene_index(
        self,
        *,
        uri: str,
        scene_index: DatasetSceneIndex,
    ) -> None:
        await self.artifact_store.write_json(uri, scene_index.to_artifact_dict())

    async def load_scene_manifest(self, uri: str) -> DatasetSceneManifest | None:
        if not await self.artifact_store.exists(uri):
            return None
        raw = await self.artifact_store.read_json(uri)
        return DatasetSceneManifest.model_validate(raw)

    async def save_scene_manifest(
        self,
        *,
        uri: str,
        manifest: DatasetSceneManifest,
    ) -> None:
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())

    async def load_sample_manifest(self, uri: str) -> DatasetSampleManifest | None:
        if not await self.artifact_store.exists(uri):
            return None
        raw = await self.artifact_store.read_json(uri)
        return DatasetSampleManifest.model_validate(raw)

    async def save_sample_manifest(
        self,
        *,
        uri: str,
        manifest: DatasetSampleManifest,
    ) -> None:
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())

    async def iter_samples(
        self,
        dataset_manifest: DatasetManifest,
        *,
        max_samples: int | None = None,
    ) -> AsyncIterator[DatasetSampleManifest]:
        scene_index = await self.load_scene_index(dataset_manifest.uris.scene_index)
        if scene_index is None:
            return

        yielded = 0

        for scene_item in scene_index.scenes:
            scene_manifest = await self.load_scene_manifest(scene_item.manifest_uri)
            if scene_manifest is None:
                continue

            for sample_id in scene_manifest.sample_ids:
                sample_uri = self.artifact_store.join_uri(
                    dataset_manifest.uris.sample_root,
                    f"{sample_id}.json",
                )
                sample_manifest = await self.load_sample_manifest(sample_uri)
                if sample_manifest is None:
                    continue

                yield sample_manifest
                yielded += 1

                if max_samples is not None and yielded >= max_samples:
                    return
