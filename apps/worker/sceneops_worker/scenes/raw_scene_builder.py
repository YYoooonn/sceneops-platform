"""RawSceneBuilder — builds SceneManifests from a RawLogFrameIndex.

This module is source-agnostic. It operates on RawLogManifest and RawLogFrameIndex
produced by a RawLogAdapter, and knows nothing about nuScenes or any other SDK.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawSensorFrameManifest,
)
from sceneops_core.scenes.schemas import (
    SampleGroupingConfig,
    SceneSegmentationConfig,
    SceneSegmentationStrategy,
)
from sceneops_core.scenes.schemas.manifests import SceneManifest
from sceneops_core.scenes.schemas.segments import SceneSegment, SceneSegmentIndex
from sceneops_worker.observations.artifacts import ObservationArtifactStore
from sceneops_worker.scenes.artifacts import SceneArtifactStore
from sceneops_worker.scenes.sample_grouping import SampleGrouper, SampleGroupingReport


@dataclass
class SceneBuildResult:
    """Return value of RawSceneBuilder.build()."""

    scene_ids: list[str]
    scene_manifest_uris: list[str]
    segment_index_uri: str
    total_samples: int
    total_frames: int
    observation_count: int
    grouping_report: SampleGroupingReport


class RawSceneBuilder:
    """Segments raw frames into scenes and produces SceneManifest artifacts."""

    def __init__(
        self,
        *,
        scene_artifact_store: SceneArtifactStore,
        observation_artifact_store: ObservationArtifactStore,
    ) -> None:
        self._scene_store = scene_artifact_store
        self._obs_store = observation_artifact_store

    async def build(
        self,
        *,
        manifest: RawLogManifest,
        frame_index: RawLogFrameIndex,
        dataset_id: str,
        dataset_version: str,
        version_root_uri: str,
        segmentation: SceneSegmentationConfig,
        sampling: SampleGroupingConfig,
        max_built_scenes: int | None = None,
    ) -> SceneBuildResult:
        """Build scenes from a raw frame index.

        Segments are computed first, then capped by max_built_scenes.
        The SceneSegmentIndex written to storage contains only the built segments.
        """
        all_segments = _segment_frames(
            frames=frame_index.frames,
            config=segmentation,
            raw_log_id=manifest.raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )

        built_segments = (
            all_segments[:max_built_scenes]
            if max_built_scenes is not None
            else all_segments
        )

        # Segment index contains only the built segments.
        segment_index = SceneSegmentIndex(
            raw_log_id=manifest.raw_log_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            segments=built_segments,
        )

        scene_ids: list[str] = []
        scene_manifest_uris: list[str] = []
        total_samples = 0
        total_frames = 0

        frame_lookup: dict[str, RawSensorFrameManifest] = {
            f.frame_id: f for f in frame_index.frames
        }

        grouper = SampleGrouper(config=sampling)
        accumulated_report = SampleGroupingReport()

        for seg in built_segments:
            seg_frames = [
                frame_lookup[fid] for fid in seg.frame_ids if fid in frame_lookup
            ]
            samples, seg_report = grouper.group(seg_frames, scene_id=seg.segment_id)
            accumulated_report.merge(seg_report)

            scene_id = seg.segment_id
            all_channels: set[str] = set()
            for s in samples:
                for sf in s.sensor_frames:
                    all_channels.add(sf.channel)

            scene_manifest = SceneManifest(
                scene_id=scene_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                samples=samples,
                sample_count=len(samples),
                frame_count=sum(len(s.sensor_frames) for s in samples),
                channels=sorted(all_channels),
                metadata={
                    "source": "raw_log",
                    "raw_log_id": manifest.raw_log_id,
                    "segment_id": seg.segment_id,
                    "source_type": str(manifest.source_type or ""),
                    "source_format": str(manifest.source_format),
                },
            )

            uri = await self._scene_store.write_scene_manifest(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                scene_id=scene_id,
                manifest=scene_manifest,
            )

            scene_ids.append(scene_id)
            scene_manifest_uris.append(uri)
            total_samples += scene_manifest.sample_count
            total_frames += scene_manifest.frame_count

        segment_index_uri = self._obs_store.scene_segments_uri(version_root_uri)
        await self._obs_store.save_scene_segment_index(
            uri=segment_index_uri, segment_index=segment_index
        )

        return SceneBuildResult(
            scene_ids=scene_ids,
            scene_manifest_uris=scene_manifest_uris,
            segment_index_uri=segment_index_uri,
            total_samples=total_samples,
            total_frames=total_frames,
            observation_count=len(frame_index.frames),
            grouping_report=accumulated_report,
        )


def _segment_frames(
    *,
    frames: list[RawSensorFrameManifest],
    config: SceneSegmentationConfig,
    raw_log_id: str,
    dataset_id: str,
    dataset_version: str,
) -> list[SceneSegment]:
    strategy = config.strategy

    if strategy == SceneSegmentationStrategy.SEQUENCE:
        return _segment_by_sequence(frames=frames, config=config, raw_log_id=raw_log_id)
    elif strategy == SceneSegmentationStrategy.GAP_BASED:
        return _segment_by_gap(frames=frames, config=config, raw_log_id=raw_log_id)
    elif strategy == SceneSegmentationStrategy.FIXED_WINDOW:
        return _segment_by_fixed_window(
            frames=frames, config=config, raw_log_id=raw_log_id
        )
    else:
        raise NotImplementedError(f"Unsupported segmentation strategy: {strategy!r}")


def _segment_by_sequence(
    *,
    frames: list[RawSensorFrameManifest],
    config: SceneSegmentationConfig,
    raw_log_id: str,
) -> list[SceneSegment]:
    """Group frames by sequence hint (source_sequence_id → source_scene_id fallback).

    Output is sorted by segment start timestamp.
    """
    grouped: dict[str, list[RawSensorFrameManifest]] = defaultdict(list)
    for f in frames:
        key = f.source_sequence_id or f.source_scene_id or "default"
        grouped[key].append(f)

    segments: list[SceneSegment] = []
    for seq_key, seq_frames in grouped.items():
        if len(seq_frames) < config.min_frame_count:
            continue

        sorted_frames = sorted(seq_frames, key=lambda f: f.timestamp_us)
        channels = sorted({f.channel for f in sorted_frames})

        segments.append(
            SceneSegment(
                segment_id=seq_key,
                raw_log_id=raw_log_id,
                start_timestamp_us=sorted_frames[0].timestamp_us,
                end_timestamp_us=sorted_frames[-1].timestamp_us,
                frame_ids=[f.frame_id for f in sorted_frames],
                channels=channels,
                segmentation=config,
            )
        )

    segments.sort(key=lambda s: s.start_timestamp_us)
    return segments


def _segment_by_gap(
    *,
    frames: list[RawSensorFrameManifest],
    config: SceneSegmentationConfig,
    raw_log_id: str,
) -> list[SceneSegment]:
    if not frames:
        return []

    sorted_frames = sorted(frames, key=lambda f: f.timestamp_us)
    max_gap_us = (config.max_timestamp_gap_ms or 500) * 1000

    segments: list[SceneSegment] = []
    current: list[RawSensorFrameManifest] = [sorted_frames[0]]

    for frame in sorted_frames[1:]:
        gap = frame.timestamp_us - current[-1].timestamp_us
        if gap > max_gap_us:
            if len(current) >= config.min_frame_count:
                segments.append(
                    _make_segment(current, raw_log_id, config, len(segments))
                )
            current = [frame]
        else:
            current.append(frame)

    if len(current) >= config.min_frame_count:
        segments.append(_make_segment(current, raw_log_id, config, len(segments)))

    return segments


def _segment_by_fixed_window(
    *,
    frames: list[RawSensorFrameManifest],
    config: SceneSegmentationConfig,
    raw_log_id: str,
) -> list[SceneSegment]:
    if not frames:
        return []

    if not config.fixed_window_duration_ms or config.fixed_window_duration_ms <= 0:
        raise ValueError(
            f"fixed_window_duration_ms must be a positive integer for fixed_window "
            f"strategy, got: {config.fixed_window_duration_ms!r}"
        )

    sorted_frames = sorted(frames, key=lambda f: f.timestamp_us)
    window_us = config.fixed_window_duration_ms * 1000
    base_us = sorted_frames[0].timestamp_us

    buckets: dict[int, list[RawSensorFrameManifest]] = defaultdict(list)
    for f in sorted_frames:
        bucket_idx = (f.timestamp_us - base_us) // window_us
        buckets[bucket_idx].append(f)

    segments: list[SceneSegment] = []
    for bucket_idx in sorted(buckets):
        bucket_frames = buckets[bucket_idx]
        if len(bucket_frames) < config.min_frame_count:
            continue
        channels = sorted({f.channel for f in bucket_frames})
        segments.append(
            SceneSegment(
                segment_id=f"{raw_log_id}-fw{bucket_idx:04d}",
                raw_log_id=raw_log_id,
                start_timestamp_us=bucket_frames[0].timestamp_us,
                end_timestamp_us=bucket_frames[-1].timestamp_us,
                frame_ids=[f.frame_id for f in bucket_frames],
                channels=channels,
                segmentation=config,
            )
        )
    return segments


def _make_segment(
    frames: list[RawSensorFrameManifest],
    raw_log_id: str,
    config: SceneSegmentationConfig,
    idx: int,
) -> SceneSegment:
    channels = sorted({f.channel for f in frames})
    return SceneSegment(
        segment_id=f"{raw_log_id}-seg{idx:04d}",
        raw_log_id=raw_log_id,
        start_timestamp_us=frames[0].timestamp_us,
        end_timestamp_us=frames[-1].timestamp_us,
        frame_ids=[f.frame_id for f in frames],
        channels=channels,
        segmentation=config,
    )
