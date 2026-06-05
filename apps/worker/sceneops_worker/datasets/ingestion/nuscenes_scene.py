from __future__ import annotations

from nuscenes.nuscenes import NuScenes

from sceneops_core.sensors import SensorModality
from sceneops_core.scenes.schemas.manifests import (
    CalibratedSensorManifest,
    EgoPoseManifest,
    SceneAnnotationManifest,
    SceneManifest,
    SceneSampleManifest,
    SceneSensorFrameManifest,
)
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_core.scenes.schemas.enums import (
    SceneGenerationMethod,
    SceneOriginType,
    SceneStatus,
)
from sceneops_core.datasets.schemas import DatasetSceneIndexEntry

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

    for idx, token in enumerate(sample_tokens):
        ns_sample = nusc.get("sample", token)
        sample_manifest = _build_sample_manifest(
            nusc=nusc,
            scene_id=scene_id,
            sample_id=f"{scene_id}-s{idx:04d}",
            sample=ns_sample,
            frame_index=idx,
        )
        samples.append(sample_manifest)
        for sf in sample_manifest.sensor_frames:
            all_channels.add(sf.channel)

    return SceneManifest(
        scene_id=scene_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        samples=samples,
        sample_count=len(samples),
        frame_count=sum(len(s.sensor_frames) for s in samples),
        channels=sorted(all_channels),
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
) -> SceneSampleManifest:
    sensor_frames: list[SceneSensorFrameManifest] = []
    calibrations: list[CalibratedSensorManifest] = []
    annotations: list[SceneAnnotationManifest] = []
    ego_pose_manifest: EgoPoseManifest | None = None

    for channel, sample_data_token in sample["data"].items():
        if channel not in _TARGET_CHANNELS:
            continue

        sample_data = nusc.get("sample_data", sample_data_token)
        cs = nusc.get("calibrated_sensor", sample_data["calibrated_sensor_token"])
        ego_pose = nusc.get("ego_pose", sample_data["ego_pose_token"])

        frame_id = f"{sample_id}-{channel}"

        sensor_frames.append(
            SceneSensorFrameManifest(
                frame_id=frame_id,
                sample_id=sample_id,
                timestamp_us=sample_data["timestamp"],
                channel=channel,
                modality=_infer_modality(channel),
                uri=sample_data["filename"],
                metadata={
                    "fileformat": sample_data.get("fileformat", ""),
                    "is_key_frame": sample_data.get("is_key_frame", False),
                    "width": sample_data.get("width"),
                    "height": sample_data.get("height"),
                },
            )
        )

        calibrations.append(
            CalibratedSensorManifest(
                calibration_id=f"{sample_id}-{channel}-calib",
                channel=channel,
                translation=cs["translation"],
                rotation=cs["rotation"],
                camera_intrinsic=cs.get("camera_intrinsic"),
            )
        )

        if ego_pose_manifest is None:
            ego_pose_manifest = EgoPoseManifest(
                ego_pose_id=f"{sample_id}-ego",
                timestamp_us=sample_data["timestamp"],
                translation=ego_pose["translation"],
                rotation=ego_pose["rotation"],
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

    return SceneSampleManifest(
        sample_id=sample_id,
        scene_id=scene_id,
        timestamp_us=sample["timestamp"],
        frame_index=frame_index,
        sensor_frames=sensor_frames,
        annotations=annotations,
        ego_pose=ego_pose_manifest,
        calibrations=calibrations,
    )


def _infer_modality(channel: str) -> SensorModality:
    if channel.startswith("CAM"):
        return SensorModality.CAMERA
    if channel.startswith("LIDAR"):
        return SensorModality.LIDAR
    if channel.startswith("RADAR"):
        return SensorModality.RADAR
    return SensorModality.UNKNOWN
