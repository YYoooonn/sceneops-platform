from __future__ import annotations

from datetime import UTC, datetime

from nuscenes.nuscenes import NuScenes
from sceneops_worker.datasets.ingestion.base import (
    DatasetIngestionRequest,
    DatasetIngestionResult,
    DatasetIngestor,
)
from sceneops_core.datasets.schemas import (
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
    DatasetIngestMode,
)
from sceneops_worker.datasets import DatasetArtifactStore

TARGET_CHANNELS = {"CAM_FRONT", "LIDAR_TOP"}

DATA_SOURCE = "nuScenes"
DATASET_TYPE = DatasetType.NUSCENES.value


class NuScenesDatasetIngestor(DatasetIngestor):
    @property
    def dataset_type(self) -> str:
        return DatasetType.NUSCENES.value

    async def run(
        self,
        request: DatasetIngestionRequest,
    ) -> DatasetIngestionResult:
        return await _ingest_nuscenes(request)


async def _ingest_nuscenes(request: DatasetIngestionRequest) -> DatasetManifest:
    source_uri = request.source_uri
    dataset_id = request.dataset_id
    dataset_version = request.dataset_version
    dataset_artifact_store = request.dataset_artifact_store
    max_scenes = request.max_scenes
    mode = request.mode

    nusc = NuScenes(
        version=dataset_version,
        dataroot=str(source_uri),
        verbose=False,
    )

    version_root_uri = dataset_artifact_store.dataset_version_root_uri(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )

    ingest_mode = DatasetIngestMode(mode)

    if ingest_mode == DatasetIngestMode.OVERWRITE:
        await dataset_artifact_store.reset_dataset_version(version_root_uri)

    existing_scene_ids = {
        item.scene_id
        for item in await _read_existing_scene_index_items(
            dataset_artifact_store=dataset_artifact_store,
            version_root_uri=version_root_uri,
        )
    }

    scenes = nusc.scene[:max_scenes] if max_scenes else nusc.scene
    scene_index_items: list[DatasetSceneIndexItem] = []

    for scene in scenes:
        scene_token = scene["token"]
        scene_name = scene["name"]

        if ingest_mode == DatasetIngestMode.APPEND and scene_name in existing_scene_ids:
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

            await dataset_artifact_store.save_sample_manifest(
                uri=dataset_artifact_store.sample_manifest_uri(
                    version_root_uri=version_root_uri,
                    sample_id=sample_id,
                ),
                manifest=sample_manifest,
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

        scene_manifest_uri = dataset_artifact_store.scene_manifest_uri(
            version_root_uri=version_root_uri,
            scene_id=scene_name,
        )

        await dataset_artifact_store.save_scene_manifest(
            uri=scene_manifest_uri,
            manifest=scene_manifest,
        )

        scene_index_items.append(
            DatasetSceneIndexItem(
                scene_id=scene_name,
                scene_token=scene_token,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                source=DATA_SOURCE,
                description=scene.get("description", ""),
                sample_count=len(sample_ids),
                status=DatasetManifestStatus.READY,
                manifest_uri=scene_manifest_uri,
            )
        )

    if ingest_mode == DatasetIngestMode.OVERWRITE:
        scene_index = DatasetSceneIndex(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source=DATA_SOURCE,
            scenes=scene_index_items,
        )

        await dataset_artifact_store.save_scene_index(
            uri=dataset_artifact_store.scene_index_uri(version_root_uri),
            scene_index=scene_index,
        )
    else:
        await _upsert_scene_index(
            dataset_artifact_store=dataset_artifact_store,
            version_root_uri=version_root_uri,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source=DATA_SOURCE,
            new_items=scene_index_items,
        )

    dataset_manifest = await _build_dataset_manifest_from_store(
        dataset_artifact_store=dataset_artifact_store,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_type=DATASET_TYPE,
        source=DATA_SOURCE,
        version_root_uri=version_root_uri,
        source_uri=source_uri,
        mode=ingest_mode.value,
        max_scenes=max_scenes,
    )

    await dataset_artifact_store.save_dataset_manifest(
        uri=dataset_manifest.uris.dataset_manifest,
        manifest=dataset_manifest,
    )

    return dataset_manifest


async def _build_dataset_manifest_from_store(
    *,
    dataset_artifact_store: DatasetArtifactStore,
    dataset_id: str,
    dataset_version: str,
    dataset_type: str,
    source: str,
    version_root_uri: str,
    source_uri: str,
    mode: str,
    max_scenes: int | None,
) -> DatasetManifest:
    scene_index = await _read_scene_index(
        dataset_artifact_store=dataset_artifact_store,
        version_root_uri=version_root_uri,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )

    total_sample_count = 0
    total_annotation_count = 0

    for scene_index_item in scene_index.scenes:
        scene_manifest = await dataset_artifact_store.load_scene_manifest(
            scene_index_item.manifest_uri
        )

        if scene_manifest is None:
            continue

        total_sample_count += scene_manifest.sample_count

        for sample_id in scene_manifest.sample_ids:
            sample_manifest = await dataset_artifact_store.load_sample_manifest(
                dataset_artifact_store.sample_manifest_uri(
                    version_root_uri=version_root_uri,
                    sample_id=sample_id,
                )
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
            manifest_root=version_root_uri,
            dataset_manifest=dataset_artifact_store.dataset_manifest_uri(
                version_root_uri
            ),
            scene_index=dataset_artifact_store.scene_index_uri(version_root_uri),
            scene_root=dataset_artifact_store.scene_root_uri(version_root_uri),
            sample_root=dataset_artifact_store.sample_root_uri(version_root_uri),
            raw_root=str(source_uri),
        ),
        ingest=DatasetIngestMetadata(
            mode=mode,
            max_scenes=max_scenes,
        ),
        metadata={},
    )


async def _read_scene_index(
    *,
    dataset_artifact_store: DatasetArtifactStore,
    version_root_uri: str,
    dataset_id: str,
    dataset_version: str,
) -> DatasetSceneIndex:
    scene_index = await dataset_artifact_store.load_scene_index(
        dataset_artifact_store.scene_index_uri(version_root_uri)
    )

    if scene_index is None:
        return DatasetSceneIndex(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            source=DATA_SOURCE,
            scenes=[],
        )

    return scene_index


async def _read_existing_scene_index_items(
    *,
    dataset_artifact_store: DatasetArtifactStore,
    version_root_uri: str,
) -> list[DatasetSceneIndexItem]:
    scene_index = await dataset_artifact_store.load_scene_index(
        dataset_artifact_store.scene_index_uri(version_root_uri)
    )

    if scene_index is None:
        return []

    return scene_index.scenes


async def _upsert_scene_index(
    *,
    dataset_artifact_store: DatasetArtifactStore,
    version_root_uri: str,
    dataset_id: str,
    dataset_version: str,
    source: str,
    new_items: list[DatasetSceneIndexItem],
) -> None:
    current = await _read_scene_index(
        dataset_artifact_store=dataset_artifact_store,
        version_root_uri=version_root_uri,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )

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

    await dataset_artifact_store.save_scene_index(
        uri=dataset_artifact_store.scene_index_uri(version_root_uri),
        scene_index=merged,
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
