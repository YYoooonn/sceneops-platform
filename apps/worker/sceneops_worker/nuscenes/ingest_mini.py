from __future__ import annotations

from pathlib import Path
from typing import Any

from nuscenes.nuscenes import NuScenes

from sceneops_worker.io.json_writer import write_json


TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}


def ingest_nuscenes_mini(
    *,
    dataroot: Path,
    version: str,
    manifest_root: Path,
    max_scenes: int | None = None,
) -> None:
    nusc = NuScenes(
        version=version,
        dataroot=str(dataroot),
        verbose=True,
    )

    scenes = nusc.scene[:max_scenes] if max_scenes else nusc.scene

    scene_index: list[dict[str, Any]] = []

    for scene in scenes:
        scene_token = scene["token"]
        scene_name = scene["name"]

        sample_tokens = _collect_sample_tokens(nusc, scene["first_sample_token"])

        scene_manifest = {
            "sceneId": scene_name,
            "sceneToken": scene_token,
            "datasetId": "nuscenes-mini",
            "datasetVersion": version,
            "description": scene.get("description", ""),
            "sampleCount": len(sample_tokens),
            "firstSampleToken": scene["first_sample_token"],
            "lastSampleToken": scene["last_sample_token"],
            "status": "READY",
            "sampleIds": [],
        }

        for index, sample_token in enumerate(sample_tokens):
            sample = nusc.get("sample", sample_token)
            sample_id = _sample_id(scene_name, index)

            sample_manifest = _build_sample_manifest(
                nusc=nusc,
                scene_id=scene_name,
                sample_id=sample_id,
                sample=sample,
                index=index,
            )

            scene_manifest["sampleIds"].append(sample_id)

            write_json(
                manifest_root / "samples" / f"{sample_id}.json",
                sample_manifest,
            )

        write_json(
            manifest_root / "scenes" / f"{scene_name}.json",
            scene_manifest,
        )

        scene_index.append(
            {
                "sceneId": scene_name,
                "sceneToken": scene_token,
                "datasetId": "nuscenes-mini",
                "datasetVersion": version,
                "description": scene.get("description", ""),
                "sampleCount": len(sample_tokens),
                "status": "READY",
            }
        )

    write_json(manifest_root / "scenes.json", scene_index)


def _collect_sample_tokens(nusc: NuScenes, first_sample_token: str) -> list[str]:
    tokens: list[str] = []

    current_token = first_sample_token

    while current_token:
        tokens.append(current_token)
        sample = nusc.get("sample", current_token)
        current_token = sample["next"]

    return tokens


def _build_sample_manifest(
    *,
    nusc: NuScenes,
    scene_id: str,
    sample_id: str,
    sample: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    sensors: dict[str, Any] = {}

    for channel, sample_data_token in sample["data"].items():
        if channel not in TARGET_CHANNELS:
            continue

        sample_data = nusc.get("sample_data", sample_data_token)
        calibrated_sensor = nusc.get(
            "calibrated_sensor",
            sample_data["calibrated_sensor_token"],
        )
        ego_pose = nusc.get("ego_pose", sample_data["ego_pose_token"])

        sensors[channel] = {
            "channel": channel,
            "sampleDataToken": sample_data_token,
            "filename": sample_data["filename"],
            "fileformat": sample_data["fileformat"],
            "isKeyFrame": sample_data["is_key_frame"],
            "width": sample_data.get("width"),
            "height": sample_data.get("height"),
            "calibratedSensor": {
                "translation": calibrated_sensor["translation"],
                "rotation": calibrated_sensor["rotation"],
                "cameraIntrinsic": calibrated_sensor.get("camera_intrinsic"),
            },
            "egoPose": {
                "translation": ego_pose["translation"],
                "rotation": ego_pose["rotation"],
            },
        }

    annotations = []

    for annotation_token in sample["anns"]:
        ann = nusc.get("sample_annotation", annotation_token)
        annotations.append(
            {
                "annotationToken": annotation_token,
                "instanceToken": ann["instance_token"],
                "categoryName": ann["category_name"],
                "translation": ann["translation"],
                "size": ann["size"],
                "rotation": ann["rotation"],
                "numLidarPts": ann["num_lidar_pts"],
                "numRadarPts": ann["num_radar_pts"],
                "visibilityToken": ann["visibility_token"],
                "attributeTokens": ann["attribute_tokens"],
            }
        )

    return {
        "sampleId": sample_id,
        "sampleToken": sample["token"],
        "sceneId": scene_id,
        "index": index,
        "timestamp": sample["timestamp"],
        "prev": sample["prev"],
        "next": sample["next"],
        "sensors": sensors,
        "annotations": annotations,
    }


def _sample_id(scene_name: str, index: int) -> str:
    return f"{scene_name}-sample-{index:04d}"
