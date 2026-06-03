from __future__ import annotations

import asyncio
from pathlib import Path

from sceneops_core.datasets.schemas import SensorFrameRole, SensorModality

from sceneops_worker.datasets.scene_building.models import IndexedRawFrame


def _modality_from_channel(channel: str) -> SensorModality:
    if channel.startswith("CAM_"):
        return SensorModality.CAMERA
    if channel.startswith("LIDAR_"):
        return SensorModality.LIDAR
    if channel.startswith("RADAR_"):
        return SensorModality.RADAR
    return SensorModality.UNKNOWN


def _role_from_modality(modality: SensorModality) -> SensorFrameRole:
    if modality == SensorModality.CAMERA:
        return SensorFrameRole.IMAGE
    if modality == SensorModality.LIDAR:
        return SensorFrameRole.POINT_CLOUD
    if modality == SensorModality.RADAR:
        return SensorFrameRole.RADAR
    return SensorFrameRole.UNKNOWN


class NuscenesRawLogIndexer:
    def __init__(
        self,
        *,
        source_uri: str,
        version: str,
        max_frames: int | None = None,
    ) -> None:
        self.source_uri = source_uri
        self.version = version
        self.max_frames = max_frames

    async def index(self) -> list[IndexedRawFrame]:
        return await asyncio.to_thread(self._index_sync)

    def _index_sync(self) -> list[IndexedRawFrame]:
        # pylint: disable=import-error, import-outside-toplevel, no-name-in-module
        from nuscenes.nuscenes import NuScenes

        nusc = NuScenes(
            version=self.version,
            dataroot=self.source_uri,
            verbose=False,
        )

        frames: list[IndexedRawFrame] = []

        for sample in nusc.sample:
            source_scene_id = sample["scene_token"]
            source_sample_id = sample["token"]

            for channel, sample_data_token in sample["data"].items():
                sample_data = nusc.get("sample_data", sample_data_token)

                modality = _modality_from_channel(channel)

                frames.append(
                    IndexedRawFrame(
                        frame_id=f"frame_{sample_data_token}",
                        timestamp_us=int(sample_data["timestamp"]),
                        channel=channel,
                        modality=modality,
                        role=_role_from_modality(modality),
                        uri=str(Path(self.source_uri) / sample_data["filename"]),
                        source_sample_id=source_sample_id,
                        source_scene_id=source_scene_id,
                        ego_pose_ref=sample_data.get("ego_pose_token"),
                        calibration_ref=sample_data.get("calibrated_sensor_token"),
                        annotation_refs=tuple(sample.get("anns", [])),
                    )
                )

                if self.max_frames is not None and len(frames) >= self.max_frames:
                    return sorted(frames, key=lambda item: item.timestamp_us)

        return sorted(frames, key=lambda item: item.timestamp_us)
