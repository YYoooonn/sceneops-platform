from __future__ import annotations

from collections.abc import AsyncIterator

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.observations.schemas.raw_logs import RawLogFrameIndex, RawLogManifest
from sceneops_core.scenes.schemas import SceneSegmentIndex
from sceneops_core.scenes.schemas.manifests import SceneManifest, SceneSampleManifest
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
    # URI helpers — positional root-based (internal use)
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

    def scene_root_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(version_root_uri, "scenes")

    def raw_root_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(version_root_uri, "raw")

    def raw_log_manifest_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(
            self.raw_root_uri(version_root_uri), "raw_log.json"
        )

    def raw_frame_index_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(
            self.raw_root_uri(version_root_uri), "frames.json"
        )

    def scene_segments_uri(self, version_root_uri: str) -> str:
        return self.artifact_store.join_uri(
            self.raw_root_uri(version_root_uri), "scene_segments.json"
        )

    # ------------------------------------------------------------------
    # URI helpers — public scene-first API
    # ------------------------------------------------------------------

    def scene_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        scene_id: str,
    ) -> str:
        version_root = self.dataset_version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(
            self.scene_root_uri(version_root), f"{scene_id}.json"
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
    # Scene manifest I/O — public scene-first API
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

    # ------------------------------------------------------------------
    # Dataset manifest I/O — public scene-first API
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

    # ------------------------------------------------------------------
    # Low-level I/O (used internally and by old callers)
    # ------------------------------------------------------------------

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

    async def load_scene_manifest(self, uri: str) -> SceneManifest | None:
        if not await self.artifact_store.exists(uri):
            return None
        raw = await self.artifact_store.read_json(uri)
        return SceneManifest.model_validate(raw)

    async def save_scene_manifest(
        self,
        *,
        uri: str,
        manifest: SceneManifest,
    ) -> None:
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())

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

    # ------------------------------------------------------------------
    # Raw log artifacts
    # ------------------------------------------------------------------

    async def save_raw_log_manifest(
        self,
        *,
        uri: str,
        manifest: RawLogManifest,
    ) -> None:
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())

    async def save_raw_frame_index(
        self,
        *,
        uri: str,
        frame_index: RawLogFrameIndex,
    ) -> None:
        await self.artifact_store.write_json(uri, frame_index.to_artifact_dict())

    async def save_scene_segment_index(
        self,
        *,
        uri: str,
        segment_index: SceneSegmentIndex,
    ) -> None:
        await self.artifact_store.write_json(uri, segment_index.to_artifact_dict())
