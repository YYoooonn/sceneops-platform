from __future__ import annotations

from sceneops_core.episodes.schemas import EpisodeManifest
from sceneops_storage import ArtifactStore


class EpisodeArtifactStore:
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

    def episode_manifest_uri(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
    ) -> str:
        version_root = self._version_root_uri(
            dataset_id=dataset_id, dataset_version=dataset_version
        )
        return self.artifact_store.join_uri(
            version_root, "episodes", f"{episode_id}.json"
        )

    # ------------------------------------------------------------------
    # Episode manifest I/O
    # ------------------------------------------------------------------

    async def write_episode_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        episode_id: str,
        manifest: EpisodeManifest,
    ) -> str:
        uri = self.episode_manifest_uri(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            episode_id=episode_id,
        )
        await self.artifact_store.write_json(uri, manifest.to_artifact_dict())
        return uri

    async def load_episode_manifest(self, uri: str) -> EpisodeManifest | None:
        if not await self.artifact_store.exists(uri):
            return None
        raw = await self.artifact_store.read_json(uri)
        return EpisodeManifest.model_validate(raw)
