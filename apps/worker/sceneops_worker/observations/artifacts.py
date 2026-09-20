from __future__ import annotations

from sceneops_core.observations.schemas.raw_logs import RawLogFrameIndex, RawLogManifest
from sceneops_core.scenes.schemas import SceneSegmentIndex
from sceneops_storage import ArtifactStore


class ObservationArtifactStore:
    """Artifact store for raw observation data (raw logs, frame indices, segments).

    Raw-log-derived artifacts are scoped by ``raw_log_id`` within a
    DatasetVersion so a second raw-log build/ingestion against the same
    dataset version cannot silently overwrite an earlier one's manifest,
    frame index, or scene segments (SceneOps V2 Request 22 / F-01).
    """

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        dataset_root_uri: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.dataset_root_uri = dataset_root_uri

    def _raw_log_root_uri(self, version_root_uri: str, raw_log_id: str) -> str:
        return self.artifact_store.join_uri(version_root_uri, "raw", raw_log_id)

    def raw_log_manifest_uri(self, version_root_uri: str, raw_log_id: str) -> str:
        return self.artifact_store.join_uri(
            self._raw_log_root_uri(version_root_uri, raw_log_id), "raw_log.json"
        )

    def raw_frame_index_uri(self, version_root_uri: str, raw_log_id: str) -> str:
        return self.artifact_store.join_uri(
            self._raw_log_root_uri(version_root_uri, raw_log_id), "frames.json"
        )

    def scene_segments_uri(self, version_root_uri: str, raw_log_id: str) -> str:
        return self.artifact_store.join_uri(
            self._raw_log_root_uri(version_root_uri, raw_log_id), "scene_segments.json"
        )

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
