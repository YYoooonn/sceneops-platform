from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from nuscenes.nuscenes import NuScenes

from sceneops_core.schemas.datasets import (
    CalibratedSensorManifest,
    DatasetIngestMetadata,
    DatasetManifest,
    DatasetManifestChannels,
    DatasetManifestStatus,
    DatasetManifestSummary,
    DatasetManifestUris,
    DatasetSampleManifest,
    DatasetSceneIndex,
    DatasetSceneIndexItem,
    DatasetSceneManifest,
    DatasetType,
    EgoPoseManifest,
    SampleAnnotationManifest,
    SampleSensorManifest,
    SensorModality,
)
from sceneops_worker.io.json_writer import write_json
from sceneops_worker.io.manifest_store import ManifestStore

TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}

DATA_SOURCE = "nuScenes"
DATASET_TYPE = DatasetType.NUSCENES.value


class IngestMode(StrEnum):
    APPEND = "append"
    OVERWRITE = "overwrite"
    UPSERT = "upsert"


def ingest_nuscenes(
    *,
    dataroot: Path,
    dataset_id: str,
    dataset_version: str,
    manifest_root: Path,
    max_scenes: int | None = None,
    mode: str = "upsert",
) -> DatasetManifest:
    raw_root = dataroot / dataset_id

    nusc = NuScenes(
        version=dataset_version,
        dataroot=str(raw_root),
        verbose=True,
    )

    version_root = _get_dataset_version_root(
        manifest_root=manifest_root,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )

    store = ManifestStore(version_root)
    ingest_mode = IngestMode(mode)

    if ingest_mode == IngestMode.OVERWRITE:
        store.reset()

    existing_scene_ids = {
        scene["scene_id"] for scene in _read_existing_scene_index_items(store)
    }

    scenes = nusc.scene[:max_scenes] if max_scenes else nusc.scene

    scene_index_items: list[DatasetSceneIndexItem] = []

    for scene in scenes:
        scene_token = scene["token"]
        scene_name = scene["name"]

        if ingest_mode == IngestMode.APPEND and scene_name in existing_scene_ids:
            continue

        sample_tokens = _collect_sample_tokens(nusc, scene["first_sample_token"])

        sample_ids: list[str] = []

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

            sample_ids.append(sample_id)

            write_json(
                version_root / "samples" / f"{sample_id}.json",
                sample_manifest.model_dump(by_alias=True, mode="json"),
            )

        scene_manifest = DatasetSceneManifest(
            scene_id=scene_name,
            scene_token=scene_token,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source=DATA_SOURCE,
            description=scene.get("description", ""),
            sample_count=len(sample_ids),
            first_sample_token=scene["first_sample_token"],
            last_sample_token=scene["last_sample_token"],
            status=DatasetManifestStatus.READY,
            sample_ids=sample_ids,
        )

        scene_manifest_uri = version_root / "scenes" / f"{scene_name}.json"

        write_json(
            scene_manifest_uri,
            scene_manifest.model_dump(by_alias=True, mode="json"),
        )

        scene_index_items.append(
            DatasetSceneIndexItem(
                scene_id=scene_name,
                scene_token=scene_token,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                source=DATA_SOURCE,
                description=scene.get("description", ""),
                sample_count=len(sample_tokens),
                status=DatasetManifestStatus.READY,
                manifest_uri=str(scene_manifest_uri),
            )
        )

    scene_index = DatasetSceneIndex(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source=DATA_SOURCE,
        scenes=scene_index_items,
    )

    if ingest_mode == IngestMode.OVERWRITE:
        store.write_json(
            "scenes.json",
            scene_index.model_dump(by_alias=True, mode="json"),
        )
    else:
        _upsert_scene_index(
            store=store,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source=DATA_SOURCE,
            new_items=scene_index_items,
        )

    dataset_manifest = _build_dataset_manifest_from_store(
        store=store,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_type=DATASET_TYPE,
        source=DATA_SOURCE,
        version_root=version_root,
        raw_root=raw_root,
        mode=ingest_mode.value,
        max_scenes=max_scenes,
    )

    store.write_json(
        "dataset.json",
        dataset_manifest.to_artifact_dict(),
    )

    return dataset_manifest


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
    dataset_type: str,
    source: str,
    version_root: Path,
    raw_root: Path,
    mode: str,
    max_scenes: int | None,
) -> DatasetManifest:
    scene_index = _read_scene_index(store)

    total_sample_count = 0
    total_annotation_count = 0

    for scene_index_item in scene_index.scenes:
        scene_manifest = _read_scene_manifest(
            store=store,
            scene_id=scene_index_item.scene_id,
        )

        if scene_manifest is None:
            continue

        total_sample_count += scene_manifest.sample_count

        for sample_id in scene_manifest.sample_ids:
            sample_manifest = _read_sample_manifest(
                store=store,
                sample_id=sample_id,
            )

            if sample_manifest is None:
                continue

            total_annotation_count += len(sample_manifest.annotations)

    return DatasetManifest(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_type=dataset_type,
        source=source,
        status=DatasetManifestStatus.READY,
        generated_at=datetime.now(UTC),
        summary=DatasetManifestSummary(
            scene_count=len(scene_index.scenes),
            sample_count=total_sample_count,
            annotation_count=total_annotation_count,
        ),
        channels=DatasetManifestChannels(
            target=sorted(TARGET_CHANNELS),
            camera=sorted(
                channel for channel in TARGET_CHANNELS if channel.startswith("CAM")
            ),
            lidar=sorted(
                channel for channel in TARGET_CHANNELS if channel.startswith("LIDAR")
            ),
            radar=sorted(
                channel for channel in TARGET_CHANNELS if channel.startswith("RADAR")
            ),
        ),
        uris=DatasetManifestUris(
            manifest_root=str(version_root),
            dataset_manifest=str(version_root / "dataset.json"),
            scene_index=str(version_root / "scenes.json"),
            scene_root=str(version_root / "scenes"),
            sample_root=str(version_root / "samples"),
            raw_root=str(raw_root),
        ),
        ingest=DatasetIngestMetadata(
            mode=mode,
            max_scenes=max_scenes,
        ),
        metadata={},
    )


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
    sample: dict,
    index: int,
    dataset_id: str,
    dataset_version: str,
) -> DatasetSampleManifest:
    sensors: dict[str, SampleSensorManifest] = {}

    for channel, sample_data_token in sample["data"].items():
        if channel not in TARGET_CHANNELS:
            continue

        sample_data = nusc.get("sample_data", sample_data_token)
        calibrated_sensor = nusc.get(
            "calibrated_sensor",
            sample_data["calibrated_sensor_token"],
        )
        ego_pose = nusc.get("ego_pose", sample_data["ego_pose_token"])

        sensors[channel] = SampleSensorManifest(
            channel=channel,
            modality=_infer_sensor_modality(channel),
            sample_data_token=sample_data_token,
            filename=sample_data["filename"],
            fileformat=sample_data["fileformat"],
            is_key_frame=sample_data["is_key_frame"],
            width=sample_data.get("width"),
            height=sample_data.get("height"),
            calibrated_sensor=CalibratedSensorManifest(
                translation=calibrated_sensor["translation"],
                rotation=calibrated_sensor["rotation"],
                camera_intrinsic=calibrated_sensor.get("camera_intrinsic"),
            ),
            ego_pose=EgoPoseManifest(
                translation=ego_pose["translation"],
                rotation=ego_pose["rotation"],
            ),
        )

    annotations: list[SampleAnnotationManifest] = []

    for annotation_token in sample["anns"]:
        ann = nusc.get("sample_annotation", annotation_token)

        annotations.append(
            SampleAnnotationManifest(
                annotation_token=annotation_token,
                instance_token=ann["instance_token"],
                category_name=ann["category_name"],
                translation=ann["translation"],
                size=ann["size"],
                rotation=ann["rotation"],
                num_lidar_pts=ann["num_lidar_pts"],
                num_radar_pts=ann["num_radar_pts"],
                visibility_token=ann["visibility_token"],
                attribute_tokens=ann["attribute_tokens"],
            )
        )

    return DatasetSampleManifest(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        sample_id=sample_id,
        sample_token=sample["token"],
        scene_id=scene_id,
        index=index,
        timestamp=sample["timestamp"],
        prev=sample["prev"],
        next=sample["next"],
        sensors=sensors,
        annotations=annotations,
    )


def _infer_sensor_modality(channel: str) -> SensorModality:
    if channel.startswith("CAM"):
        return SensorModality.CAMERA

    if channel.startswith("LIDAR"):
        return SensorModality.LIDAR

    if channel.startswith("RADAR"):
        return SensorModality.RADAR

    return SensorModality.UNKNOWN


def _sample_id(scene_name: str, index: int) -> str:
    return f"{scene_name}-sample-{index:04d}"


def _read_scene_index(store: ManifestStore) -> DatasetSceneIndex:
    raw = store.read_json("scenes.json")

    if raw is None:
        return DatasetSceneIndex(
            dataset_id="",
            dataset_version="",
            source=DATA_SOURCE,
            scenes=[],
        )

    if isinstance(raw, list):
        items = [DatasetSceneIndexItem.model_validate(item) for item in raw]
        dataset_id = items[0].dataset_id if items else ""
        dataset_version = items[0].dataset_version if items else ""

        return DatasetSceneIndex(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source=DATA_SOURCE,
            scenes=items,
        )

    return DatasetSceneIndex.model_validate(raw)


def _read_existing_scene_index_items(store: ManifestStore) -> list[dict]:
    scene_index = _read_scene_index(store)
    return [item.model_dump(by_alias=True, mode="json") for item in scene_index.scenes]


def _upsert_scene_index(
    *,
    store: ManifestStore,
    dataset_id: str,
    dataset_version: str,
    source: str,
    new_items: list[DatasetSceneIndexItem],
) -> None:
    current = _read_scene_index(store)

    item_by_scene_id = {item.scene_id: item for item in current.scenes}

    for item in new_items:
        item_by_scene_id[item.scene_id] = item

    merged = DatasetSceneIndex(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source=source,
        scenes=sorted(
            item_by_scene_id.values(),
            key=lambda item: item.scene_id,
        ),
    )

    store.write_json(
        "scenes.json",
        merged.model_dump(by_alias=True, mode="json"),
    )


def _read_scene_manifest(
    *,
    store: ManifestStore,
    scene_id: str,
) -> DatasetSceneManifest | None:
    raw = store.read_json(f"scenes/{scene_id}.json")

    if raw is None:
        return None

    return DatasetSceneManifest.model_validate(raw)


def _read_sample_manifest(
    *,
    store: ManifestStore,
    sample_id: str,
) -> DatasetSampleManifest | None:
    raw = store.read_json(f"samples/{sample_id}.json")

    if raw is None:
        return None

    return DatasetSampleManifest.model_validate(raw)
