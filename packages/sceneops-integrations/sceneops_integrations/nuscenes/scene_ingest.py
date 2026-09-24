"""nuScenes-SDK-bound direct SceneManifest ingestion (SceneOps V2 Request
4.6B).

Migrated from ``apps/worker/sceneops_worker/datasets/ingestion/
nuscenes_scene.py`` (``build_scene_manifest`` and its helpers, unchanged
logic) plus the scene-iteration/filtering loop from
``apps/worker/sceneops_worker/jobs/dataset/ingest_scenes.py``'s
``_ingest_nuscenes_scenes``, now living where every other nuScenes-SDK-bound
code lives (``sceneops_integrations.nuscenes``, Request 4.4/4.5) instead of
inside the worker process. Reached through the same ``runtime.execute()``
entrypoint as ``raw_log.py`` (``config["mode"] == "scene_manifest"``, see
``runtime.py``), so it shares the same HTTP/container transport
(``service.py``/``entrypoint.py``) -- no new transport was added for this.

This is a genuinely distinct capability from ``raw_log.py``: it builds one
canonical ``SceneManifest`` per real nuScenes scene directly, INCLUDING
ground-truth annotations (bounding boxes, velocity, attributes) from
nuScenes' own ``sample_annotation`` records -- the raw-log ->
``BUILD_SCENES`` flow never has and still does not produce annotations at
all. Removing this path would remove the only source of ground-truth
scenes in the repository (consumed downstream by
``sceneops_worker.evaluation.detection`` and
``sceneops_worker.jobs.scenarios``), which is why Request 4.6B migrates it
instead of deleting it.

May depend on: ``sceneops-core`` (schemas), an ``ArtifactStore``
implementation, and the ``nuscenes-devkit`` SDK (imported lazily). Must
never depend on ``sceneops-db``, worker job/Celery context, or
artifact-record registration -- exactly like ``raw_log.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.artifacts.contracts import ArtifactStore
from sceneops_core.scenes.schemas.manifests import (
    SceneAnnotationManifest,
    SceneManifest,
    SceneSampleManifest,
    SceneSensorFrameManifest,
)
from sceneops_core.sensors import SensorModality
from sceneops_core.sensors.manifests import (
    EgoPoseManifest,
    ImageMetadataManifest,
    SensorCalibrationManifest,
)

_TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}


@dataclass(frozen=True)
class IngestedScene:
    scene_id: str
    manifest: SceneManifest
    manifest_uri: str


async def ingest_nuscenes_scenes(
    *,
    artifact_store: ArtifactStore,
    source_root_uri: str,
    source_format_version: str,
    dataset_id: str,
    dataset_version: str,
    scene_manifest_root_uri: str,
    source_scene_ids: list[str] | None = None,
    max_source_scenes: int | None = None,
) -> list[IngestedScene]:
    """Parse a local nuScenes dataroot into one ``SceneManifest`` per real
    nuScenes scene (with ground-truth annotations) and persist each to
    ``artifact_store`` under ``scene_manifest_root_uri`` -- the same
    ``{scene_id}.json`` naming ``SceneArtifactStore.scene_manifest_uri``
    already uses, so scenes registered from this path land at identical
    URIs to before this extraction.

    ``source_scene_ids`` filters by nuScenes scene name before
    ``max_source_scenes`` truncates -- same order Request 3.x's
    ``_ingest_nuscenes_scenes`` always applied them in.
    """
    from nuscenes.nuscenes import NuScenes

    nusc = NuScenes(
        version=source_format_version,
        dataroot=source_root_uri,
        verbose=False,
    )

    scenes = nusc.scene
    if source_scene_ids:
        scene_names = set(source_scene_ids)
        scenes = [s for s in scenes if s["name"] in scene_names]
    if max_source_scenes is not None:
        scenes = scenes[:max_source_scenes]

    results: list[IngestedScene] = []

    for ns_scene in scenes:
        scene_id = ns_scene["name"]

        manifest = build_scene_manifest(
            nusc=nusc,
            scene=ns_scene,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
        )

        manifest_uri = artifact_store.join_uri(
            scene_manifest_root_uri, f"{scene_id}.json"
        )
        await artifact_store.write_json(manifest_uri, manifest.to_artifact_dict())

        results.append(
            IngestedScene(
                scene_id=scene_id, manifest=manifest, manifest_uri=manifest_uri
            )
        )

    return results


def build_scene_manifest(
    *,
    nusc,
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


def _collect_sample_tokens(nusc, first_token: str) -> list[str]:
    tokens: list[str] = []
    current = first_token
    while current:
        tokens.append(current)
        sample = nusc.get("sample", current)
        current = sample["next"]
    return tokens


def _resolve_attribute_names(*, nusc, ann: dict) -> list[str]:
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


def _safe_box_velocity(*, nusc, annotation_token: str) -> list[float] | None:
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
    *, nusc, sample: dict, sample_id: str
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
    *, nusc, sample: dict, sample_id: str, annotation_ids: list[str]
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
    *, nusc, scene_id: str, sample_id: str, sample: dict, frame_index: int
) -> tuple[
    SceneSampleManifest,
    dict[str, SensorCalibrationManifest],
    dict[str, EgoPoseManifest],
]:
    annotations = _build_sample_annotations(
        nusc=nusc, sample=sample, sample_id=sample_id
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


def _to_sensor_modality(*, raw_modality: str, channel: str) -> SensorModality:
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


__all__ = ["IngestedScene", "ingest_nuscenes_scenes", "build_scene_manifest"]
