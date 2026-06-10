from __future__ import annotations

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawLogSourceFormat,
    RawLogSourceType,
    RawSensorFrameManifest,
    TimeRange,
)
from sceneops_core.observations.schemas.frames import (
    RawCalibrationManifest,
    RawEgoPoseManifest,
)
from sceneops_core.sensors import SensorModality
from sceneops_worker.observations.artifacts import ObservationArtifactStore

_TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}
_OBJECT_STORAGE_SCHEMES = ("s3://", "gs://", "gcs://", "minio://", "az://", "abfs://")


class NuScenesRawLogMocker:
    """Adapts nuScenes mini dataset into generic raw log artifacts.

    Implements RawLogAdapter interface so BuildScenesJobHandler can treat it
    identically to any other raw log source.

    Timestamp semantics:
      RawSensorFrameManifest.timestamp_us <- sample_data["timestamp"]
      RawEgoPoseManifest.timestamp_us     <- ego_pose["timestamp"]
      RawCalibrationManifest              <- no timestamp
      nuScenes sample                     <- traversal only, not emitted as RawSample
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

    @staticmethod
    def _is_object_storage_uri(uri: str) -> bool:
        return any(uri.startswith(scheme) for scheme in _OBJECT_STORAGE_SCHEMES)

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

        from nuscenes.nuscenes import NuScenes

        nusc = NuScenes(
            version=dataset_version,
            dataroot=self._source_root_uri,
            verbose=False,
        )

        frames: list[RawSensorFrameManifest] = []
        calibrations_by_id: dict[str, RawCalibrationManifest] = {}
        ego_poses_by_id: dict[str, RawEgoPoseManifest] = {}

        all_channels: set[str] = set()
        all_modalities: set[SensorModality] = set()
        source_sequences: set[str] = set()
        min_ts: int | None = None
        max_ts: int | None = None

        max_source_seqs: int | None = params.get("max_source_sequences") or None

        for ns_scene in nusc.scene:
            sequence_id = ns_scene["name"]

            if max_source_seqs is not None and len(source_sequences) >= max_source_seqs:
                break

            source_sequences.add(sequence_id)
            token = ns_scene["first_sample_token"]

            while token:
                ns_sample = nusc.get("sample", token)

                for channel, sd_token in ns_sample["data"].items():
                    # if channel not in self._required_channels:
                    #     continue

                    sd = nusc.get("sample_data", sd_token)
                    cs_token = sd["calibrated_sensor_token"]
                    ep_token = sd["ego_pose_token"]

                    cs = nusc.get("calibrated_sensor", cs_token)
                    ep = nusc.get("ego_pose", ep_token)

                    modality = _infer_modality(channel)

                    frame_ts = sd["timestamp"]

                    if min_ts is None or frame_ts < min_ts:
                        min_ts = frame_ts
                    if max_ts is None or frame_ts > max_ts:
                        max_ts = frame_ts

                    frame = RawSensorFrameManifest(
                        frame_id=sd_token,
                        timestamp_us=frame_ts,
                        channel=channel,
                        modality=modality,
                        uri=sd["filename"],
                        sequence_id=sequence_id,
                        sensor_id=cs["sensor_token"],
                        metadata={
                            "source": "nuscenes",
                            "scene_token": ns_scene["token"],
                            "scene_name": ns_scene["name"],
                            "sample_token_hint": ns_sample["token"],
                            "sample_timestamp_us_hint": ns_sample["timestamp"],
                            "sample_data_token": sd_token,
                            "calibrated_sensor_token_hint": cs_token,
                            "ego_pose_token_hint": ep_token,
                            "width": sd.get("width"),
                            "height": sd.get("height"),
                            "fileformat": sd.get("fileformat"),
                            "is_key_frame": sd.get("is_key_frame", False),
                        },
                    )
                    frames.append(frame)

                    if cs_token not in calibrations_by_id:
                        calibrations_by_id[cs_token] = RawCalibrationManifest(
                            calibration_id=cs_token,
                            sensor_id=cs["sensor_token"],
                            channel=channel,
                            modality=modality,
                            translation=cs.get("translation"),
                            rotation=cs.get("rotation"),
                            rotation_format="quaternion_wxyz",
                            camera_intrinsic=cs.get("camera_intrinsic") or None,
                            metadata={
                                "source": "nuscenes",
                                "calibrated_sensor_token": cs_token,
                                "sensor_token": cs["sensor_token"],
                            },
                        )

                    if ep_token not in ego_poses_by_id:
                        ego_poses_by_id[ep_token] = RawEgoPoseManifest(
                            ego_pose_id=ep_token,
                            timestamp_us=ep["timestamp"],
                            translation=ep.get("translation"),
                            rotation=ep.get("rotation"),
                            rotation_format="quaternion_wxyz",
                            metadata={
                                "source": "nuscenes",
                                "ego_pose_token": ep_token,
                            },
                        )

                    all_channels.add(channel)
                    all_modalities.add(modality)

                token = ns_sample["next"]

        calibrations = list(calibrations_by_id.values())
        ego_poses = list(ego_poses_by_id.values())

        frame_index_uri = self._observation_store.raw_frame_index_uri(version_root_uri)

        frame_index = RawLogFrameIndex(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            frames=frames,
            calibrations=calibrations,
            ego_poses=ego_poses,
        )

        manifest = RawLogManifest(
            raw_log_id=raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            dataset_type="nuscenes",
            source_format=RawLogSourceFormat.NUSCENES,
            source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
            root_uri=self._source_root_uri,
            channels=sorted(all_channels),
            modalities=sorted(m.value for m in all_modalities),
            frame_count=len(frames),
            calibration_count=len(calibrations),
            ego_pose_count=len(ego_poses),
            sequence_count=len(source_sequences),
            time_range=(
                TimeRange(
                    start_timestamp_us=min_ts,
                    end_timestamp_us=max_ts,
                )
                if min_ts is not None and max_ts is not None
                else None
            ),
            frame_index_uri=frame_index_uri,
        )

        manifest_uri = self._observation_store.raw_log_manifest_uri(version_root_uri)

        await self._observation_store.save_raw_log_manifest(
            uri=manifest_uri,
            manifest=manifest,
        )
        await self._observation_store.save_raw_frame_index(
            uri=frame_index_uri,
            frame_index=frame_index,
        )

        return manifest, frame_index, manifest_uri, frame_index_uri


def _infer_modality(channel: str) -> SensorModality:
    if channel.startswith("CAM"):
        return SensorModality.CAMERA
    if channel.startswith("LIDAR"):
        return SensorModality.LIDAR
    if channel.startswith("RADAR"):
        return SensorModality.RADAR
    return SensorModality.UNKNOWN
