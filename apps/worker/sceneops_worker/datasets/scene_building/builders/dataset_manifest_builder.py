from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sceneops_core.datasets.schemas import (
    DatasetIngestMetadata,
    DatasetManifest,
    DatasetManifestChannels,
    DatasetManifestStatus,
    DatasetManifestSummary,
    DatasetManifestUris,
    DatasetSceneIndex,
    DatasetSceneIndexItem,
    DatasetSceneManifest,
    DatasetSampleManifest,
    DatasetType,
    SceneSegmentManifest,
)

from sceneops_core.ids import generate_prefixed_id

from sceneops_worker.datasets import DatasetArtifactStore
from sceneops_worker.datasets.scene_building.models import IndexedRawFrame


class BuiltDatasetManifests:
    def __init__(
        self,
        *,
        dataset_manifest: DatasetManifest,
        scene_index: DatasetSceneIndex,
        scenes: list[DatasetSceneManifest],
        samples: list[DatasetSampleManifest],
    ) -> None:
        self.dataset_manifest = dataset_manifest
        self.scene_index = scene_index
        self.scenes = scenes
        self.samples = samples


class SceneSegmentDatasetManifestBuilder:
    def __init__(
        self,
        *,
        artifact_store: DatasetArtifactStore,
        dataset_id: str,
        dataset_version: str,
        dataset_type: DatasetType,
        source: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.dataset_id = dataset_id
        self.dataset_version = dataset_version
        self.dataset_type = dataset_type
        self.source = source

    def build(
        self,
        *,
        version_root_uri: str,
        raw_root_uri: str,
        frames: list[IndexedRawFrame],
        segments: list[SceneSegmentManifest],
        max_scenes: int | None = None,
    ) -> BuiltDatasetManifests:
        frames_by_id = {frame.frame_id: frame for frame in frames}

        scene_root_uri = self.artifact_store.scene_root_uri(version_root_uri)
        sample_root_uri = self.artifact_store.sample_root_uri(version_root_uri)
        scene_index_uri = self.artifact_store.scene_index_uri(version_root_uri)
        dataset_manifest_uri = self.artifact_store.dataset_manifest_uri(
            version_root_uri
        )

        scenes: list[DatasetSceneManifest] = []
        samples: list[DatasetSampleManifest] = []
        scene_index_items: list[DatasetSceneIndexItem] = []

        selected_segments = (
            segments[:max_scenes] if max_scenes is not None else segments
        )

        for scene_number, segment in enumerate(selected_segments):
            scene_id = f"scene_{scene_number:06d}"

            segment_frames = [
                frames_by_id[frame_id]
                for frame_id in segment.frame_ids
                if frame_id in frames_by_id
            ]

            scene_samples = self._build_samples(
                scene_id=scene_id,
                frames=segment_frames,
            )
            samples.extend(scene_samples)

            scene_manifest_uri = self.artifact_store.scene_manifest_uri(
                version_root_uri=version_root_uri,
                scene_id=scene_id,
            )

            scene_manifest = DatasetSceneManifest(
                scene_id=scene_id,
                scene_token=segment.segment_id,
                dataset_id=self.dataset_id,
                dataset_version=self.dataset_version,
                source=self.source,
                description=f"Built from raw log segment {segment.segment_id}",
                sample_count=len(scene_samples),
                first_sample_token=scene_samples[0].sample_token
                if scene_samples
                else None,
                last_sample_token=scene_samples[-1].sample_token
                if scene_samples
                else None,
                status=DatasetManifestStatus.READY,
                sample_ids=[sample.sample_id for sample in scene_samples],
                metadata={
                    "raw_log_id": segment.raw_log_id,
                    "segment_id": segment.segment_id,
                    "start_timestamp_us": segment.start_timestamp_us,
                    "end_timestamp_us": segment.end_timestamp_us,
                    "channels": segment.channels,
                    "policy": segment.policy.to_artifact_dict(),
                    "quality_summary": segment.quality_summary,
                },
            )
            scenes.append(scene_manifest)

            scene_index_items.append(
                DatasetSceneIndexItem(
                    scene_id=scene_id,
                    scene_token=segment.segment_id,
                    dataset_id=self.dataset_id,
                    dataset_version=self.dataset_version,
                    source=self.source,
                    description=scene_manifest.description,
                    sample_count=scene_manifest.sample_count,
                    status=DatasetManifestStatus.READY,
                    manifest_uri=scene_manifest_uri,
                )
            )

        scene_index = DatasetSceneIndex(
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            source=self.source,
            scenes=scene_index_items,
        )

        channels = sorted({frame.channel for frame in frames})
        camera_channels = sorted(
            {frame.channel for frame in frames if frame.modality.value == "camera"}
        )
        lidar_channels = sorted(
            {frame.channel for frame in frames if frame.modality.value == "lidar"}
        )
        radar_channels = sorted(
            {frame.channel for frame in frames if frame.modality.value == "radar"}
        )

        dataset_manifest = DatasetManifest(
            dataset_id=self.dataset_id,
            dataset_version=self.dataset_version,
            dataset_type=self.dataset_type,
            source=self.source,
            status=DatasetManifestStatus.READY,
            generated_at=datetime.now(UTC),
            summary=DatasetManifestSummary(
                scene_count=len(scenes),
                sample_count=len(samples),
                annotation_count=0,
            ),
            channels=DatasetManifestChannels(
                target=channels,
                camera=camera_channels,
                lidar=lidar_channels,
                radar=radar_channels,
            ),
            uris=DatasetManifestUris(
                manifest_root=version_root_uri,
                dataset_manifest=dataset_manifest_uri,
                scene_index=scene_index_uri,
                scene_root=scene_root_uri,
                sample_root=sample_root_uri,
                raw_root=raw_root_uri,
            ),
            ingest=DatasetIngestMetadata(
                mode="build_scenes",
                max_scenes=max_scenes,
            ),
            metadata={
                "scene_building": {
                    "source": self.source,
                    "segment_count": len(selected_segments),
                }
            },
        )

        return BuiltDatasetManifests(
            dataset_manifest=dataset_manifest,
            scene_index=scene_index,
            scenes=scenes,
            samples=samples,
        )

    def _build_samples(
        self,
        *,
        scene_id: str,
        frames: list[IndexedRawFrame],
    ) -> list[DatasetSampleManifest]:
        # 실제 DatasetSampleManifest 필드명에 맞춰 조정 필요.
        # 여기서는 timestamp bucket 기반 sample grouping만 설계로 둔다.
        buckets: dict[int, list[IndexedRawFrame]] = defaultdict(list)

        for frame in frames:
            bucket = int(frame.timestamp_us / 100_000)  # 100ms bucket
            buckets[bucket].append(frame)

        samples: list[DatasetSampleManifest] = []

        for _, bucket_frames in sorted(buckets.items()):
            sample_id = generate_prefixed_id("sample")
            timestamp_us = min(frame.timestamp_us for frame in bucket_frames)

            samples.append(
                DatasetSampleManifest(
                    sample_id=sample_id,
                    sample_token=sample_id,
                    scene_id=scene_id,
                    dataset_id=self.dataset_id,
                    dataset_version=self.dataset_version,
                    timestamp_us=timestamp_us,
                    channels=sorted({frame.channel for frame in bucket_frames}),
                    sensor_data=[
                        {
                            "frame_id": frame.frame_id,
                            "channel": frame.channel,
                            "modality": frame.modality.value,
                            "role": frame.role.value,
                            "uri": frame.uri,
                            "ego_pose_ref": frame.ego_pose_ref,
                            "calibration_ref": frame.calibration_ref,
                            "annotation_refs": list(frame.annotation_refs),
                            "source_sample_id": frame.source_sample_id,
                            "source_scene_id": frame.source_scene_id,
                        }
                        for frame in bucket_frames
                    ],
                    metadata={
                        "built_from_raw_log": True,
                    },
                )
            )

        return samples
