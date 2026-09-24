from __future__ import annotations

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.observations.schemas import RawLogFrameIndex, RawLogManifest
from sceneops_worker.integrations.nuscenes.raw_log import (
    is_object_storage_uri,
    read_nuscenes_raw_log,
)
from sceneops_worker.observations.artifacts import ObservationArtifactStore

_TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}


class NuScenesRawLogMocker:
    """Worker-side ``RawLogAdapter`` for nuScenes (SceneOps V2 Request 4.4).

    Implements the ``RawLogAdapter`` interface so ``BuildScenesJobHandler``
    can treat it identically to any other raw log source. The SDK-bound
    parsing itself now lives in
    ``sceneops_worker.integrations.nuscenes.raw_log.read_nuscenes_raw_log``
    -- this class is a thin adapter that:

    * resolves ``params['source_format_version']``/
      ``params['max_source_sequences']`` (the ``RawLogAdapter`` protocol's
      ``params: dict`` convention) and rejects a missing/empty
      ``source_format_version`` with no fallback to ``dataset_version``
      (SceneOps V2 Request 3.2B/3.2B.1);
    * resolves the two destination URIs via ``ObservationArtifactStore``
      (SceneOps' own raw-log-artifact layout policy, Request 22/F-01);
    * delegates the actual nuScenes SDK read + ``ArtifactStore`` write to
      ``read_nuscenes_raw_log``.

    Behavior (traversal order, timestamp semantics, metadata fields,
    manifest/frame-index content, the object-storage guard) is unchanged
    from before this extraction -- see ``raw_log.py`` for the preserved
    logic itself.
    """

    def __init__(
        self,
        *,
        source_store: ArtifactStore,
        source_root_uri: str,
        observation_store: ObservationArtifactStore,
        required_channels: set[str] | None = None,
    ) -> None:
        self._source_store = source_store
        self._source_root_uri = source_root_uri
        self._observation_store = observation_store
        self._required_channels = required_channels or _TARGET_CHANNELS

    _is_object_storage_uri = staticmethod(is_object_storage_uri)

    async def build_raw_log(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        raw_log_id: str,
        version_root_uri: str,
        params: dict,
    ) -> tuple[RawLogManifest, RawLogFrameIndex, str, str]:
        if self._is_object_storage_uri(self._source_root_uri):
            raise NotImplementedError(
                "Storage-backed nuScenes raw source is not implemented yet. "
                "NuScenesRawLogMocker currently requires a local filesystem dataroot. "
                f"Received raw source root URI: {self._source_root_uri}"
            )

        source_format_version = params.get("source_format_version")
        if not source_format_version:
            raise ValueError(
                "params['source_format_version'] is required for "
                "NuScenesRawLogMocker.build_raw_log -- refusing to fall "
                f"back to dataset_version={dataset_version!r}"
            )

        max_source_seqs: int | None = params.get("max_source_sequences") or None

        frame_index_uri = self._observation_store.raw_frame_index_uri(
            version_root_uri, raw_log_id
        )
        manifest_uri = self._observation_store.raw_log_manifest_uri(
            version_root_uri, raw_log_id
        )

        manifest, frame_index = await read_nuscenes_raw_log(
            artifact_store=self._observation_store.artifact_store,
            source_root_uri=self._source_root_uri,
            source_format_version=source_format_version,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            raw_log_id=raw_log_id,
            manifest_uri=manifest_uri,
            frame_index_uri=frame_index_uri,
            max_source_sequences=max_source_seqs,
        )

        return manifest, frame_index, manifest_uri, frame_index_uri
