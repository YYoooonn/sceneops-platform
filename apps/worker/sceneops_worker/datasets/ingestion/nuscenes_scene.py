from __future__ import annotations

from nuscenes.nuscenes import NuScenes

from sceneops_core.scenes.schemas.manifests import (
    SceneAnnotationManifest,
    SceneManifest,
    SceneSampleManifest,
    SceneSensorFrameManifest,
)
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
    annotation_count: int = 0

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
        annotation_count += len(sample_manifest.annotations)
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
        annotation_count=annotation_count,
        has_ground_truth=annotation_count > 0,
        ground_truth_source="nuscenes" if annotation_count > 0 else None,
        channels=sorted(all_channels),
        start_timestamp_us=min_ts,
        end_timestamp_us=max_ts,
        metadata={
            "source": "nuscenes",
            "nuscenes_scene_name": scene["name"],
            "nuscenes_scene_token": scene["token"],
            "description": scene.get("description", ""),
            "sample_source": "nuscenes_sample",
            "annotation_count": annotation_count,
        },
    )


def _collect_sample_tokens(nusc: NuScenes, first_token: str) -> list[str]:
    tokens: list[str] = []
    current = first_token
    while current:
        tokens.append(current)
        sample = nusc.get("sample", current)
        current = sample["next"]
    return tokens


def _resolve_attribute_names(
    *,
    nusc: NuScenes,
    ann: dict,
) -> list[str]:
    names: list[str] = []

    for token in ann.get("attribute_tokens", []):
        try:
            attr = nusc.get("attribute", token)
            name = attr.get("name")
            if name:
                names.append(name)
        except Exception:
            continue

    return names


def _safe_box_velocity(
    *,
    nusc: NuScenes,
    annotation_token: str,
) -> list[float] | None:
    try:
        velocity = nusc.box_velocity(annotation_token)
    except Exception:
        return None

    if velocity is None:
        return None

    values = [float(v) for v in velocity]
    if any(v != v for v in values):  # NaN check
        return None

    return values


def _build_sample_annotations(
    *,
    nusc: NuScenes,
    sample: dict,
    sample_id: str,
) -> list[SceneAnnotationManifest]:
    annotations: list[SceneAnnotationManifest] = []

    for ann_token in sample.get("anns", []):
        ann = nusc.get("sample_annotation", ann_token)

        annotations.append(
            SceneAnnotationManifest(
                annotation_id=ann_token,
                sample_id=sample_id,
                source_annotation_id=ann_token,
                source_sample_id=sample["token"],
                category=ann["category_name"],
                instance_id=ann["instance_token"],
                timestamp_us=sample["timestamp"],
                coordinate_frame="world",
                translation=ann["translation"],
                size=ann["size"],
                rotation=ann["rotation"],
                rotation_format="quaternion_wxyz",
                velocity=_safe_box_velocity(nusc=nusc, annotation_token=ann_token),
                attributes=_resolve_attribute_names(nusc=nusc, ann=ann),
                num_lidar_points=ann.get("num_lidar_pts", 0),
                num_radar_points=ann.get("num_radar_pts", 0),
                metadata={
                    "source": "nuscenes",
                    "visibility_token": ann.get("visibility_token", ""),
                    "attribute_tokens": ann.get("attribute_tokens", []),
                },
            )
        )

    return annotations


def _build_sample_sensor_frames(
    *,
    nusc: NuScenes,
    sample: dict,
    sample_id: str,
    annotation_ids: list[str],
) -> tuple[
    list[SceneSensorFrameManifest],
    dict[str, SensorCalibrationManifest],
    dict[str, EgoPoseManifest],
]:
    sensor_frames: list[SceneSensorFrameManifest] = []
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

        modality = _to_sensor_modality(
            raw_modality=sensor.get("modality", "unknown"),
            channel=channel,
        )

        calibrated_sensors_by_id[cs_token] = SensorCalibrationManifest(
            calibration_id=cs_token,
            sensor_id=cs["sensor_token"],
            channel=channel,
            modality=modality,
            translation=cs["translation"],
            rotation=cs["rotation"],
            rotation_format="quaternion_wxyz",
            camera_intrinsic=cs.get("camera_intrinsic") or None,
            metadata={
                "source": "nuscenes",
                "nuscenes_sensor_token": cs["sensor_token"],
                "nuscenes_calibrated_sensor_token": cs_token,
            },
        )

        ego_poses_by_id[ep_token] = EgoPoseManifest(
            ego_pose_id=ep_token,
            timestamp_us=ep.get("timestamp"),
            translation=ep["translation"],
            rotation=ep["rotation"],
            rotation_format="quaternion_wxyz",
            metadata={
                "source": "nuscenes",
                "nuscenes_ego_pose_token": ep_token,
            },
        )

        image = (
            ImageMetadataManifest(
                width=sample_data.get("width") or None,
                height=sample_data.get("height") or None,
                fileformat=sample_data.get("fileformat"),
            )
            if modality == SensorModality.CAMERA
            else None
        )

        sensor_frames.append(
            SceneSensorFrameManifest(
                frame_id=sample_data_token,
                sample_id=sample_id,
                timestamp_us=sample_data["timestamp"],
                channel=channel,
                modality=modality,
                uri=sample_data["filename"],
                calibration_id=cs_token,
                ego_pose_id=ep_token,
                image=image,
                annotation_ids=annotation_ids,
                metadata={
                    "source": "nuscenes",
                    "source_sample_id": sample["token"],
                    "source_sample_data_id": sample_data_token,
                    "is_key_frame": sample_data.get("is_key_frame", False),
                },
            )
        )

    return sensor_frames, calibrated_sensors_by_id, ego_poses_by_id


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
    annotations = _build_sample_annotations(
        nusc=nusc,
        sample=sample,
        sample_id=sample_id,
    )
    annotation_ids = [ann.annotation_id for ann in annotations]

    sensor_frames, calibrated_sensors_by_id, ego_poses_by_id = (
        _build_sample_sensor_frames(
            nusc=nusc, sample=sample, sample_id=sample_id, annotation_ids=annotation_ids
        )
    )
    return (
        SceneSampleManifest(
            sample_id=sample_id,
            scene_id=scene_id,
            timestamp_us=sample["timestamp"],
            frame_index=frame_index,
            sensor_frames=sensor_frames,
            annotations=annotations,
            metadata={
                "source": "nuscenes",
                "source_sample_id": sample["token"],
                "source_sample_timestamp_us": sample["timestamp"],
            },
        ),
        calibrated_sensors_by_id,
        ego_poses_by_id,
    )


def _to_sensor_modality(
    *,
    raw_modality: str,
    channel: str,
) -> SensorModality:
    try:
        return SensorModality(raw_modality)
    except ValueError:
        if channel.startswith("CAM"):
            return SensorModality.CAMERA
        if channel.startswith("LIDAR"):
            return SensorModality.LIDAR
        if channel.startswith("RADAR"):
            return SensorModality.RADAR
        return SensorModality.UNKNOWN
