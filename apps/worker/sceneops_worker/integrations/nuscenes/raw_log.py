"""nuScenes-SDK-bound raw-log reader (SceneOps V2 Request 4.4).

Extracted from ``sceneops_worker.datasets.ingestion.nuscenes_raw_log.
NuScenesRawLogMocker.build_raw_log`` unchanged in behavior: same traversal
order, same timestamp semantics, same metadata fields, same generated
``RawLogManifest``/``RawLogFrameIndex`` content. What moved is *shape*, not
*logic* -- this module is now the SDK-bound leaf (nuScenes SDK + local
filesystem dataroot + ``ArtifactStore`` only), with no dependency on
``sceneops-db``, a worker ``JobHandlerRequest``/``WorkerContext``, Celery, or
``ArtifactRecord`` registration. ``NuScenesRawLogMocker`` (the
``RawLogAdapter`` the worker's ``RawLogAdapterFactory`` still registers) is
now a thin wrapper around ``read_nuscenes_raw_log`` below, so
``BuildScenesJobHandler``'s call site and existing tests are unaffected.

Timestamp semantics (unchanged from the original docstring):
  RawSensorFrameManifest.timestamp_us <- sample_data["timestamp"]
  RawEgoPoseManifest.timestamp_us     <- ego_pose["timestamp"]
  RawCalibrationManifest              <- no timestamp
  nuScenes sample                     <- traversal only, not emitted as RawSample

May depend on: ``sceneops-core`` (schemas), an ``ArtifactStore``
implementation (``sceneops-storage``, or any ``ArtifactStore``-shaped
object), and the ``nuscenes-devkit`` SDK (imported lazily, only once a local
dataroot has been confirmed). Must never depend on ``sceneops-db``, worker
job/Celery context, or artifact-record registration.
"""

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

_OBJECT_STORAGE_SCHEMES = ("s3://", "gs://", "gcs://", "minio://", "az://", "abfs://")


def is_object_storage_uri(uri: str) -> bool:
    """Whether ``uri`` points at object storage rather than a local path.

    Storage-backed nuScenes raw sources are not implemented yet --
    ``read_nuscenes_raw_log`` requires a local filesystem dataroot, matching
    ``nuscenes-devkit``'s own ``NuScenes(dataroot=...)`` contract.
    """
    return any(uri.startswith(scheme) for scheme in _OBJECT_STORAGE_SCHEMES)


def infer_modality(channel: str) -> SensorModality:
    if channel.startswith("CAM"):
        return SensorModality.CAMERA
    if channel.startswith("LIDAR"):
        return SensorModality.LIDAR
    if channel.startswith("RADAR"):
        return SensorModality.RADAR
    return SensorModality.UNKNOWN


async def read_nuscenes_raw_log(
    *,
    artifact_store: ArtifactStore,
    source_root_uri: str,
    source_format_version: str,
    dataset_id: str,
    dataset_version: str,
    raw_log_id: str,
    manifest_uri: str,
    frame_index_uri: str,
    max_source_sequences: int | None = None,
) -> tuple[RawLogManifest, RawLogFrameIndex]:
    """Parse a local nuScenes dataroot into ``RawLogManifest`` +
    ``RawLogFrameIndex`` and persist both to ``artifact_store`` at the given
    URIs (the same two writes ``ObservationArtifactStore.
    save_raw_log_manifest``/``save_raw_frame_index`` performed before this
    extraction -- URI *scoping* policy stays with the caller, which already
    owns dataset-version-rooted artifact layout; this function only knows
    the two destination URIs it's handed).

    ``source_format_version`` is nuScenes' own on-disk version folder name
    (e.g. ``"v1.0-mini"``) -- the caller is responsible for resolving it
    (never ``dataset_version``, SceneOps' own canonical identity) and for
    rejecting an empty/missing value before calling this function.
    """
    if is_object_storage_uri(source_root_uri):
        raise NotImplementedError(
            "Storage-backed nuScenes raw source is not implemented yet. "
            "read_nuscenes_raw_log currently requires a local filesystem "
            f"dataroot. Received raw source root URI: {source_root_uri}"
        )

    from nuscenes.nuscenes import NuScenes

    nusc = NuScenes(
        version=source_format_version,
        dataroot=source_root_uri,
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

    for ns_scene in nusc.scene:
        sequence_id = ns_scene["name"]

        if (
            max_source_sequences is not None
            and len(source_sequences) >= max_source_sequences
        ):
            break

        source_sequences.add(sequence_id)
        token = ns_scene["first_sample_token"]

        while token:
            ns_sample = nusc.get("sample", token)

            for channel, sd_token in ns_sample["data"].items():
                sd = nusc.get("sample_data", sd_token)
                cs_token = sd["calibrated_sensor_token"]
                ep_token = sd["ego_pose_token"]

                cs = nusc.get("calibrated_sensor", cs_token)
                ep = nusc.get("ego_pose", ep_token)

                modality = infer_modality(channel)

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
        root_uri=source_root_uri,
        channels=sorted(all_channels),
        modalities=sorted(m.value for m in all_modalities),
        frame_count=len(frames),
        calibration_count=len(calibrations),
        ego_pose_count=len(ego_poses),
        sequence_count=len(source_sequences),
        time_range=(
            TimeRange(start_timestamp_us=min_ts, end_timestamp_us=max_ts)
            if min_ts is not None and max_ts is not None
            else None
        ),
        frame_index_uri=frame_index_uri,
    )

    await artifact_store.write_json(manifest_uri, manifest.to_artifact_dict())
    await artifact_store.write_json(frame_index_uri, frame_index.to_artifact_dict())

    return manifest, frame_index


__all__ = ["is_object_storage_uri", "infer_modality", "read_nuscenes_raw_log"]
