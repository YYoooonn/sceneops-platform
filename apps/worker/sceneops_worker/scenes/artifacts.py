from __future__ import annotations

from collections.abc import AsyncIterator

from sceneops_core.datasets.schemas import DatasetManifest, DatasetSceneIndexEntry
from sceneops_core.scenes.schemas.manifests import SceneManifest, SceneSampleManifest
from sceneops_storage import ArtifactStore


class SceneArtifactStore:
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

    def _version_root_uri(self, *, dataset_id: str, dataset_version: str) -> str:
        return self.artifact_store.join_uri(
            self.dataset_root_uri,
            dataset_id,
            "versions",
            dataset_version,
        )

    def scene_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ) -> str:
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(version_root, "scenes", f"{scene_id}.json")

    def scene_index_uri(self, *, dataset_id: str, dataset_version: str) -> str:
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(version_root, "scene_index.json")

    # ------------------------------------------------------------------
    # Scene manifest I/O
    # ------------------------------------------------------------------

    async def write_scene_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
        manifest: SceneManifest,
    ) -> str:
        uri = self.scene_manifest_uri(
            dataset_id=dataset_id, dataset_version=dataset_version, scene_id=scene_id
        )
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())
        return uri

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

    async def load_scene_manifest(self, uri: str) -> SceneManifest | None:
        if not await self.artifact_store.exists(uri):
            return None
        raw = await self.artifact_store.read_json(uri)
        return SceneManifest.model_validate(raw)

    async def read_scene_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ) -> SceneManifest | None:
        uri = self.scene_manifest_uri(
            dataset_id=dataset_id, dataset_version=dataset_version, scene_id=scene_id
        )
        return await self.load_scene_manifest(uri)

    async def iter_samples(
        self,
        dataset_manifest: DatasetManifest,
        *,
        max_samples: int | None = None,
    ) -> AsyncIterator[SceneSampleManifest]:
        yielded = 0

        for scene_entry in dataset_manifest.scenes:
            scene_manifest = await self.load_scene_manifest(
                scene_entry.scene_manifest_uri
            )
            if scene_manifest is None:
                continue

            for sample in scene_manifest.samples:
                yield sample
                yielded += 1

                if max_samples is not None and yielded >= max_samples:
                    return
