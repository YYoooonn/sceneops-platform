from __future__ import annotations

from collections import Counter

from sceneops_core.schemas.datasets import DatasetManifest
from sceneops_core.schemas.datasets.profile import (
    DatasetAnnotationProfile,
    DatasetChannelProfile,
    DatasetProfileReport,
    DatasetProfileScope,
    DatasetProfileSummary,
    DatasetSceneProfile,
)
from sceneops_worker.datasets import DatasetArtifactStore


async def profile_dataset(
    *,
    profile_run_id: str,
    job_id: str,
    dataset_id: str,
    dataset_version: str,
    dataset_manifest_uri: str,
    dataset_manifest: DatasetManifest,
    dataset_artifact_store: DatasetArtifactStore,
    required_channels: list[str],
    scope: DatasetProfileScope,
    max_samples: int | None = None,
    profile_samples: bool = True,
    profile_annotations: bool = True,
    profile_sensor_coverage: bool = True,
    profile_scene_distribution: bool = True,
) -> DatasetProfileReport:
    scene_index = await dataset_artifact_store.load_scene_index(
        dataset_manifest.uris.scene_index
    )

    observed_channels: set[str] = set()
    channel_counts: Counter[str] = Counter()
    channel_modalities: dict[str, str] = {}

    class_distribution: Counter[str] = Counter()

    scene_profiles: list[DatasetSceneProfile] = []

    profiled_scene_count = 0
    profiled_sample_count = 0

    annotation_count = 0
    empty_annotation_sample_count = 0
    missing_required_channel_count = 0

    reached_limit = False

    for scene_item in scene_index.scenes:
        if reached_limit:
            break

        scene_manifest = await dataset_artifact_store.load_scene_manifest(
            scene_item.manifest_uri
        )
        if scene_manifest is None:
            continue

        profiled_scene_count += 1

        scene_sample_count = 0
        scene_annotation_count = 0
        scene_channel_counts: Counter[str] = Counter()

        for sample_id in scene_manifest.sample_ids:
            if max_samples is not None and profiled_sample_count >= max_samples:
                reached_limit = True
                break

            sample_uri = dataset_artifact_store.sample_manifest_uri(
                version_root_uri=dataset_manifest.uris.manifest_root,
                sample_id=sample_id,
            )
            sample_manifest = await dataset_artifact_store.load_sample_manifest(
                sample_uri
            )
            if sample_manifest is None:
                continue

            profiled_sample_count += 1
            scene_sample_count += 1

            sensors = sample_manifest.sensors or {}

            if profile_sensor_coverage:
                for channel, sensor in sensors.items():
                    observed_channels.add(channel)
                    channel_counts[channel] += 1
                    scene_channel_counts[channel] += 1

                    modality = getattr(sensor, "modality", None)
                    if modality is not None:
                        channel_modalities[channel] = str(modality)

                for channel in required_channels:
                    if channel not in sensors:
                        missing_required_channel_count += 1

            annotations = sample_manifest.annotations or []
            sample_annotation_count = len(annotations)

            annotation_count += sample_annotation_count
            scene_annotation_count += sample_annotation_count

            if profile_annotations:
                if sample_annotation_count == 0:
                    empty_annotation_sample_count += 1

                for annotation in annotations:
                    category_name = getattr(annotation, "category_name", None)
                    if category_name is None:
                        category_name = getattr(annotation, "category", None)
                    if category_name is None:
                        category_name = "unknown"

                    class_distribution[str(category_name)] += 1

        if profile_scene_distribution:
            scene_profiles.append(
                DatasetSceneProfile(
                    scene_id=scene_item.scene_id,
                    sample_count=scene_sample_count,
                    annotation_count=scene_annotation_count,
                    channel_counts=dict(scene_channel_counts),
                )
            )

    observed_channel_list = sorted(observed_channels)

    total_required_slots = profiled_sample_count * len(required_channels)

    if total_required_slots > 0:
        sensor_coverage_ratio = (
            total_required_slots - missing_required_channel_count
        ) / total_required_slots
    else:
        sensor_coverage_ratio = 0.0

    if profiled_sample_count > 0:
        empty_annotation_sample_ratio = (
            empty_annotation_sample_count / profiled_sample_count
        )
    else:
        empty_annotation_sample_ratio = 0.0

    channels = [
        DatasetChannelProfile(
            channel=channel,
            modality=channel_modalities.get(channel),
            sample_count=count,
            missing_count=max(0, profiled_sample_count - count),
            coverage_ratio=(
                count / profiled_sample_count if profiled_sample_count > 0 else 0.0
            ),
        )
        for channel, count in sorted(channel_counts.items())
    ]

    summary = DatasetProfileSummary(
        scene_count=dataset_manifest.summary.scene_count,
        sample_count=dataset_manifest.summary.sample_count,
        annotation_count=dataset_manifest.summary.annotation_count,
        profiled_scene_count=profiled_scene_count,
        profiled_sample_count=profiled_sample_count,
        observed_channel_count=len(observed_channel_list),
        missing_required_channel_count=missing_required_channel_count,
        sensor_coverage_ratio=sensor_coverage_ratio,
        empty_annotation_sample_count=empty_annotation_sample_count,
        empty_annotation_sample_ratio=empty_annotation_sample_ratio,
    )

    return DatasetProfileReport(
        profile_run_id=profile_run_id,
        job_id=job_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_manifest_uri=dataset_manifest_uri,
        scope=scope,
        max_samples=max_samples,
        required_channels=required_channels,
        observed_channels=observed_channel_list,
        summary=summary,
        channels=channels,
        scenes=scene_profiles,
        annotations=DatasetAnnotationProfile(
            total_count=annotation_count,
            class_distribution=dict(class_distribution),
            empty_sample_count=empty_annotation_sample_count,
            empty_sample_ratio=empty_annotation_sample_ratio,
        ),
        metadata={
            "profile_samples": profile_samples,
            "profile_annotations": profile_annotations,
            "profile_sensor_coverage": profile_sensor_coverage,
            "profile_scene_distribution": profile_scene_distribution,
            "max_samples": max_samples,
        },
    )
