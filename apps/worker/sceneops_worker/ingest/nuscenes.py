from __future__ import annotations

from pathlib import Path
from typing import Any
from enum import StrEnum

from nuscenes.nuscenes import NuScenes

from sceneops_worker.io.json_writer import write_json
from sceneops_worker.io.manifest_store import ManifestStore


TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}

DATA_SOURCE = "nuScenes"


class IngestMode(StrEnum):
    REPLACE = "replace"
    APPEND = "append"
    UPSERT = "upsert"


def ingest_nuscenes(
    *,
    dataroot: Path,
    dataset_id: str,
    dataset_version: str,
    manifest_root: Path,
    max_scenes: int | None = None,
    mode: IngestMode = IngestMode.UPSERT,
) -> None:
    nusc = NuScenes(
        version=dataset_version,
        dataroot=str(dataroot / dataset_id),
        verbose=True,
    )

    version_root = _get_dataset_version_root(
        manifest_root=manifest_root,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )

    store = ManifestStore(version_root)
    existing_scene_ids = {scene["sceneId"] for scene in store.read_scene_index()}

    if mode == IngestMode.REPLACE:
        store.reset()

    scenes = nusc.scene[:max_scenes] if max_scenes else nusc.scene

    scene_index: list[dict[str, Any]] = []
    total_sample_count = 0
    total_annotation_count = 0

    for scene in scenes:
        scene_token = scene["token"]
        scene_name = scene["name"]

        if mode == IngestMode.APPEND and scene_name in existing_scene_ids:
            continue

        sample_tokens = _collect_sample_tokens(nusc, scene["first_sample_token"])
        total_sample_count += len(sample_tokens)

        scene_manifest = {
            "sceneId": scene_name,
            "sceneToken": scene_token,
            "datasetId": dataset_id,
            "datasetVersion": dataset_version,
            "source": DATA_SOURCE,
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
                dataset_id=dataset_id,
                dataset_version=dataset_version,
            )

            total_annotation_count += len(sample_manifest["annotations"])

            scene_manifest["sampleIds"].append(sample_id)

            write_json(
                version_root / "samples" / f"{sample_id}.json",
                sample_manifest,
            )

        write_json(
            version_root / "scenes" / f"{scene_name}.json",
            scene_manifest,
        )

        scene_index.append(
            {
                "sceneId": scene_name,
                "sceneToken": scene_token,
                "datasetId": dataset_id,
                "datasetVersion": dataset_version,
                "source": DATA_SOURCE,
                "description": scene.get("description", ""),
                "sampleCount": len(sample_tokens),
                "status": "READY",
            }
        )

    if mode == IngestMode.REPLACE:
        merged_scene_index = scene_index
        store.write_json("scenes.json", merged_scene_index)
    else:
        merged_scene_index = store.upsert_scene_index(scene_index)

    dataset_manifest = _build_dataset_manifest_from_store(
        store=store,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )

    store.write_json("dataset.json", dataset_manifest)


def _get_dataset_version_root(
    *,
    manifest_root: Path,
    dataset_id: str,
    dataset_version: str,
) -> Path:
    return manifest_root / "datasets" / dataset_id / "versions" / dataset_version


def _build_dataset_manifest_from_store(
    *,
    store: ManifestStore,
    dataset_id: str,
    dataset_version: str,
) -> dict[str, Any]:
    scenes = store.read_scene_index()

    total_sample_count = 0
    total_annotation_count = 0

    for scene_index_item in scenes:
        scene_id = scene_index_item["sceneId"]
        scene_manifest = store.read_json(f"scenes/{scene_id}.json")

        if scene_manifest is None:
            continue

        total_sample_count += int(scene_manifest.get("sampleCount", 0))

        for sample_id in scene_manifest.get("sampleIds", []):
            sample_manifest = store.read_json(f"samples/{sample_id}.json")
            if sample_manifest is None:
                continue

            total_annotation_count += len(sample_manifest.get("annotations", []))

    return {
        "datasetId": dataset_id,
        "datasetVersion": dataset_version,
        "source": DATA_SOURCE,
        "status": "READY",
        "sceneCount": len(scenes),
        "sampleCount": total_sample_count,
        "annotationCount": total_annotation_count,
        "targetChannels": sorted(TARGET_CHANNELS),
    }


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
    dataset_id: str,
    dataset_version: str,
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
        "datasetId": dataset_id,
        "datasetVersion": dataset_version,
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
