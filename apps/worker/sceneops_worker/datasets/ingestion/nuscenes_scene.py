from __future__ import annotations

from nuscenes.nuscenes import NuScenes

from sceneops_core.datasets.schemas import DatasetSceneIndexEntry
from sceneops_core.scenes.schemas.enums import (
    SceneGenerationMethod,
    SceneOriginType,
    SceneStatus,
)
from sceneops_core.scenes.schemas.manifests import (
    SceneAnnotationManifest,
    SceneManifest,
    SceneSampleManifest,
    SceneSensorFrameManifest,
)
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.sensors import SensorModality
from sceneops_core.sensors.manifests import (
    ImageMetadataManifest,
    SensorCalibrationManifest,
    EgoPoseManifest,
)

_TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}


def build_scene_manifest(
    *,
    nusc: NuScenes,
    scene: dict,
    dataset_id: str,
    dataset_version: str,
    scene_id: str,
) -> SceneManifest:
    sample_tokens = _collect_sample_tokens(nusc, scene["first_sample_token"])
    samples: list[SceneSampleManifest] = []
    all_channels: set[str] = set()

    calibrated_sensors_by_id: dict[str, SensorCalibrationManifest] = {}
    ego_poses_by_id: dict[str, EgoPoseManifest] = {}

    min_ts: int | None = None
    max_ts: int | None = None

    for idx, token in enumerate(sample_tokens):
        ns_sample = nusc.get("sample", token)
        sample_ts = ns_sample["timestamp"]  # canonical sample timestamp

        if min_ts is None or sample_ts < min_ts:
            min_ts = sample_ts
        if max_ts is None or sample_ts > max_ts:
            max_ts = sample_ts

        sample_manifest, cal_records, ego_records = _build_sample_manifest(
            nusc=nusc,
            scene_id=scene_id,
            sample_id=f"{scene_id}-s{idx:04d}",
            sample=ns_sample,
            frame_index=idx,
        )
        samples.append(sample_manifest)
        calibrated_sensors_by_id.update(cal_records)
        ego_poses_by_id.update(ego_records)
        for sf in sample_manifest.sensor_frames:
            all_channels.add(sf.channel)

    return SceneManifest(
        scene_id=scene_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        calibrated_sensors=list(calibrated_sensors_by_id.values()),
        ego_poses=list(ego_poses_by_id.values()),
        samples=samples,
        sample_count=len(samples),
        frame_count=sum(len(s.sensor_frames) for s in samples),
        channels=sorted(all_channels),
        start_timestamp_us=min_ts,
        end_timestamp_us=max_ts,
        metadata={
            "source": "nuscenes",
            "nuscenes_scene_name": scene["name"],
            "nuscenes_scene_token": scene["token"],
            "description": scene.get("description", ""),
        },
    )


def build_scene_record(
    *,
    scene_id: str,
    dataset_id: str,
    dataset_version: str,
    manifest: SceneManifest,
    scene_manifest_uri: str,
) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        status=SceneStatus.BUILT,
        origin_type=SceneOriginType.REAL,
        generation_method=SceneGenerationMethod.UNKNOWN,
        scene_manifest_uri=scene_manifest_uri,
        sample_count=manifest.sample_count,
        frame_count=manifest.frame_count,
        channels=manifest.channels,
        metadata=manifest.metadata,
    )


def build_scene_index_entry(
    *,
    scene_id: str,
    scene_manifest_uri: str,
    manifest: SceneManifest,
) -> DatasetSceneIndexEntry:
    return DatasetSceneIndexEntry(
        scene_id=scene_id,
        scene_manifest_uri=scene_manifest_uri,
        sample_count=manifest.sample_count,
        frame_count=manifest.frame_count,
        channels=manifest.channels,
    )


def _collect_sample_tokens(nusc: NuScenes, first_token: str) -> list[str]:
    tokens: list[str] = []
    current = first_token
    while current:
        tokens.append(current)
        sample = nusc.get("sample", current)
        current = sample["next"]
    return tokens


def _build_sample_manifest(
    *,
    nusc: NuScenes,
    scene_id: str,
    sample_id: str,
    sample: dict,
    frame_index: int,
) -> tuple[
    SceneSampleManifest,
    dict[str, SensorCalibrationManifest],
    dict[str, EgoPoseManifest],
]:
    sensor_frames: list[SceneSensorFrameManifest] = []
    annotations: list[SceneAnnotationManifest] = []
    calibrated_sensors_by_id: dict[str, SensorCalibrationManifest] = {}
    ego_poses_by_id: dict[str, EgoPoseManifest] = {}

    for channel, sample_data_token in sample["data"].items():
        if channel not in _TARGET_CHANNELS:
            continue

        sample_data = nusc.get("sample_data", sample_data_token)
        cs_token = sample_data["calibrated_sensor_token"]
        ep_token = sample_data["ego_pose_token"]

        cs = nusc.get("calibrated_sensor", cs_token)
        ep = nusc.get("ego_pose", ep_token)
        sensor = nusc.get("sensor", cs["sensor_token"])

        nusc_modality: str = sensor.get("modality", "unknown")
        try:
            modality = SensorModality(nusc_modality)
        except ValueError:
            modality = _infer_modality(channel)

        calibrated_sensor = SensorCalibrationManifest(
            calibration_id=cs_token,
            sensor_id=cs["sensor_token"],
            channel=channel,
            modality=modality,
            translation=cs["translation"],
            rotation=cs["rotation"],
            rotation_format="quaternion_wxyz",
            camera_intrinsic=cs.get("camera_intrinsic") or None,
            metadata={
                "nuscenes_sensor_token": cs["sensor_token"],
                "nuscenes_calibrated_sensor_token": cs_token,
            },
        )
        calibrated_sensors_by_id[cs_token] = calibrated_sensor

        # ego_pose timestamp from ego_pose["timestamp"], not sample_data["timestamp"]
        ego_pose = EgoPoseManifest(
            ego_pose_id=ep_token,
            timestamp_us=ep.get("timestamp"),
            translation=ep["translation"],
            rotation=ep["rotation"],
            rotation_format="quaternion_wxyz",
            metadata={
                "nuscenes_ego_pose_token": ep_token,
            },
        )
        ego_poses_by_id[ep_token] = ego_pose

        image = (
            ImageMetadataManifest(
                width=sample_data.get("width") or None,
                height=sample_data.get("height") or None,
                fileformat=sample_data.get("fileformat"),
            )
            if nusc_modality == "camera"
            else None
        )

        sensor_frames.append(
            SceneSensorFrameManifest(
                frame_id=f"{sample_id}-{channel}",
                sample_id=sample_id,
                timestamp_us=sample_data["timestamp"],  # sample_data timestamp
                channel=channel,
                modality=modality,
                uri=sample_data["filename"],
                calibration_id=cs_token,
                ego_pose_id=ep_token,
                image=image,
                metadata={
                    "is_key_frame": sample_data.get("is_key_frame", False),
                },
            )
        )

    for ann_token in sample.get("anns", []):
        ann = nusc.get("sample_annotation", ann_token)
        annotations.append(
            SceneAnnotationManifest(
                annotation_id=ann_token,
                sample_id=sample_id,
                source_annotation_id=ann_token,
                category=ann["category_name"],
                instance_id=ann["instance_token"],
                translation=ann["translation"],
                size=ann["size"],
                rotation=ann["rotation"],
                metadata={
                    "num_lidar_pts": ann.get("num_lidar_pts", 0),
                    "num_radar_pts": ann.get("num_radar_pts", 0),
                    "visibility_token": ann.get("visibility_token", ""),
                },
            )
        )

    return (
        SceneSampleManifest(
            sample_id=sample_id,
            scene_id=scene_id,
            timestamp_us=sample["timestamp"],  # canonical sample timestamp
            frame_index=frame_index,
            sensor_frames=sensor_frames,
            annotations=annotations,
        ),
        calibrated_sensors_by_id,
        ego_poses_by_id,
    )


def _infer_modality(channel: str) -> SensorModality:
    if channel.startswith("CAM"):
        return SensorModality.CAMERA
    if channel.startswith("LIDAR"):
        return SensorModality.LIDAR
    if channel.startswith("RADAR"):
        return SensorModality.RADAR
    return SensorModality.UNKNOWN
