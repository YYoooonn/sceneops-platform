from __future__ import annotations

from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawLogSourceFormat,
    RawLogSourceType,
    RawSensorFrameManifest,
    TimeRange,
)
from sceneops_core.sensors import SensorModality
from sceneops_worker.observations.artifacts import ObservationArtifactStore

_TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}


class NuScenesRawLogMocker:
    """Adapts nuScenes mini dataset into generic raw log artifacts.

    Implements RawLogAdapter interface so BuildScenesJobHandler can treat it
    identically to any other raw log source.
    """

    def __init__(
        self,
        *,
        source_root_uri: str,
        observation_store: ObservationArtifactStore,
        required_channels: set[str] | None = None,
    ) -> None:
        self._source_root_uri = source_root_uri
        self._observation_store = observation_store
        self._required_channels = required_channels or _TARGET_CHANNELS

    async def build_raw_log(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        raw_log_id: str,
        version_root_uri: str,
        params: dict,
    ) -> tuple[RawLogManifest, RawLogFrameIndex, str, str]:
        from nuscenes.nuscenes import NuScenes

        nusc = NuScenes(
            version=dataset_version,
            dataroot=self._source_root_uri,
            verbose=False,
        )

        frames: list[RawSensorFrameManifest] = []
        all_channels: set[str] = set()
        source_scenes = set()
        min_ts: int | None = None
        max_ts: int | None = None

        # max_source_sequences limits how many source sequences/scenes are read.
        max_source_seqs: int | None = params.get("max_source_sequences") or None

        for ns_scene in nusc.scene:
            if max_source_seqs is not None and len(source_scenes) >= max_source_seqs:
                break
            source_scenes.add(ns_scene["name"])
            token = ns_scene["first_sample_token"]

            while token:
                ns_sample = nusc.get("sample", token)
                sample_ts = ns_sample["timestamp"]

                if min_ts is None or sample_ts < min_ts:
                    min_ts = sample_ts
                if max_ts is None or sample_ts > max_ts:
                    max_ts = sample_ts

                for channel, sd_token in ns_sample["data"].items():
                    if channel not in self._required_channels:
                        continue

                    sd = nusc.get("sample_data", sd_token)
                    cs_token = sd["calibrated_sensor_token"]

                    frame = RawSensorFrameManifest(
                        frame_id=sd_token,
                        timestamp_us=sd["timestamp"],
                        channel=channel,
                        modality=_infer_modality(channel),
                        uri=sd["filename"],
                        # nuScenes-specific aliases
                        source_scene_id=ns_scene["name"],
                        source_sample_id=ns_sample["token"],
                        # Generic raw-log identifiers
                        source_sequence_id=ns_scene["name"],
                        source_frame_id=ns_sample["token"],
                        source_sensor_id=cs_token,
                        metadata={
                            "nuscenes_scene_token": ns_scene["token"],
                            "nuscenes_sample_token": ns_sample["token"],
                            "fileformat": sd.get("fileformat", ""),
                            "is_key_frame": sd.get("is_key_frame", False),
                        },
                    )
                    frames.append(frame)
                    all_channels.add(channel)

                token = ns_sample["next"]

        manifest = RawLogManifest(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            dataset_type="nuscenes",
            source_format=RawLogSourceFormat.NUSCENES,
            source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
            root_uri=self._source_root_uri,
            channels=sorted(all_channels),
            frame_count=len(frames),
            observation_count=len(frames),
            source_sequence_count=len(source_scenes),
            time_range=TimeRange(
                start_timestamp_us=min_ts or 0,
                end_timestamp_us=max_ts or 0,
            )
            if min_ts is not None
            else None,
        )

        frame_index = RawLogFrameIndex(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            frames=frames,
        )

        manifest_uri = self._observation_store.raw_log_manifest_uri(version_root_uri)
        frame_index_uri = self._observation_store.raw_frame_index_uri(version_root_uri)

        await self._observation_store.save_raw_log_manifest(
            uri=manifest_uri, manifest=manifest
        )
        await self._observation_store.save_raw_frame_index(
            uri=frame_index_uri, frame_index=frame_index
        )

        manifest = manifest.model_copy(update={"frame_index_uri": frame_index_uri})

        return manifest, frame_index, manifest_uri, frame_index_uri


def _infer_modality(channel: str) -> SensorModality:
    if channel.startswith("CAM"):
        return SensorModality.CAMERA
    if channel.startswith("LIDAR"):
        return SensorModality.LIDAR
    if channel.startswith("RADAR"):
        return SensorModality.RADAR
    return SensorModality.UNKNOWN
