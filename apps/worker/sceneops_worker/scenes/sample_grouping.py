"""SampleGrouper — groups raw sensor frames into SceneSampleManifests.

Source-agnostic. Receives a flat list of RawSensorFrameManifest objects
(already belonging to one scene segment) and applies the configured
SampleGroupingStrategy.

Implemented strategies:
  FRAME_ID          — groups frames by source_frame_id / source_sample_id fallback.
  TIME_BUCKET       — fixed time windows anchored at the first frame's timestamp.
  NEAREST_TIMESTAMP — reference_channel drives sample cadence; other channels
                      are matched to each reference frame by nearest timestamp.
                      sync_policy controls inclusion strictness.

Missing channel handling (required_channels + missing_channel_policy):
  Fully implemented. Supports KEEP_WITH_WARNING, DROP_SAMPLE, FAIL_SCENE.
  When required_channels is empty the report mirrors Phase 2 behaviour:
  before == after == total, filter counts are zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.scenes.schemas import (
    MissingChannelPolicy,
    SampleGroupingConfig,
    SampleGroupingStrategy,
    SensorSyncPolicy,
)
from sceneops_core.scenes.schemas.manifests import (
    SceneSampleManifest,
    SceneSensorFrameManifest,
)


@dataclass
class SampleGroupingReport:
    """Counters produced by one SampleGrouper.group() call.

    merge() accumulates reports from multiple segments in-place.
    Drop/warn/missing fields are populated when required_channels is set.
    """

    total_samples_built: int = 0
    sample_count_before_filtering: int = 0
    sample_count_after_filtering: int = 0
    dropped_sample_count: int = 0
    warned_sample_count: int = 0
    samples_with_missing_channels_count: int = 0
    missing_channel_counts_by_channel: dict[str, int] = field(default_factory=dict)

    def merge(self, other: SampleGroupingReport) -> None:
        """Accumulate another report's counts into this one in-place."""
        self.total_samples_built += other.total_samples_built
        self.sample_count_before_filtering += other.sample_count_before_filtering
        self.sample_count_after_filtering += other.sample_count_after_filtering
        self.dropped_sample_count += other.dropped_sample_count
        self.warned_sample_count += other.warned_sample_count
        self.samples_with_missing_channels_count += (
            other.samples_with_missing_channels_count
        )
        for channel, count in other.missing_channel_counts_by_channel.items():
            self.missing_channel_counts_by_channel[channel] = (
                self.missing_channel_counts_by_channel.get(channel, 0) + count
            )


class SampleGrouper:
    """Groups raw sensor frames into scene samples using the configured strategy."""

    def __init__(self, config: SampleGroupingConfig) -> None:
        self.config = config

    def group(
        self,
        frames: list[RawSensorFrameManifest],
        *,
        scene_id: str,
    ) -> tuple[list[SceneSampleManifest], SampleGroupingReport]:
        """Group frames into samples and apply missing_channel_policy if configured.

        Returns (samples, report).

        When required_channels is empty the report mirrors Phase 2 behaviour:
        before == after == total, all filter counts are 0.

        When required_channels is set, _apply_missing_channel_policy runs after
        grouping and populates the drop/warn/missing report fields.
        """
        strategy = self.config.strategy

        if strategy == SampleGroupingStrategy.FRAME_ID:
            samples = self._group_by_frame_id(frames, scene_id=scene_id)
        elif strategy == SampleGroupingStrategy.TIME_BUCKET:
            if (
                not self.config.sample_time_window_ms
                or self.config.sample_time_window_ms <= 0
            ):
                raise ValueError(
                    f"sample_time_window_ms must be a positive number for time_bucket "
                    f"strategy, got: {self.config.sample_time_window_ms!r}"
                )
            samples = self._group_by_time_bucket(
                frames,
                scene_id=scene_id,
                window_ms=self.config.sample_time_window_ms,
            )
        elif strategy == SampleGroupingStrategy.NEAREST_TIMESTAMP:
            samples = self._group_by_nearest_timestamp(frames, scene_id=scene_id)
        else:
            raise NotImplementedError(f"Unsupported sampling strategy: {strategy!r}")

        n = len(samples)
        report = SampleGroupingReport(
            total_samples_built=n,
            sample_count_before_filtering=n,
            sample_count_after_filtering=n,
        )

        if self.config.required_channels:
            samples = self._apply_missing_channel_policy(
                samples, report=report, scene_id=scene_id
            )

        return samples, report

    # ── strategy implementations ───────────────────────────────────────────────

    def _group_by_frame_id(
        self,
        frames: list[RawSensorFrameManifest],
        *,
        scene_id: str,
    ) -> list[SceneSampleManifest]:
        """Group by source hint: source_frame_id → source_sample_id → frame_id.

        Groups are ordered by the minimum timestamp of their constituent frames.
        """
        grouped: dict[str, list[RawSensorFrameManifest]] = defaultdict(list)
        for f in frames:
            key = f.source_frame_id or f.source_sample_id or f.frame_id
            grouped[key].append(f)

        sorted_groups = sorted(
            grouped.values(), key=lambda grp: min(f.timestamp_us for f in grp)
        )

        samples: list[SceneSampleManifest] = []
        for idx, sample_frames in enumerate(sorted_groups):
            sample_id = f"{scene_id}-sample-{idx:06d}"
            ts = min(f.timestamp_us for f in sample_frames)

            sensor_frames = [
                SceneSensorFrameManifest(
                    frame_id=f.frame_id,
                    sample_id=sample_id,
                    timestamp_us=f.timestamp_us,
                    channel=f.channel,
                    modality=f.modality,
                    uri=f.uri,
                    metadata=f.metadata,
                )
                for f in sample_frames
            ]

            samples.append(
                SceneSampleManifest(
                    sample_id=sample_id,
                    scene_id=scene_id,
                    timestamp_us=ts,
                    frame_index=idx,
                    sensor_frames=sensor_frames,
                )
            )

        return samples

    def _group_by_time_bucket(
        self,
        frames: list[RawSensorFrameManifest],
        *,
        scene_id: str,
        window_ms: float,
    ) -> list[SceneSampleManifest]:
        """Group into fixed time buckets anchored at the first frame's timestamp."""
        if not frames:
            return []

        window_us = int(window_ms * 1000)
        sorted_frames = sorted(frames, key=lambda f: f.timestamp_us)
        base_us = sorted_frames[0].timestamp_us

        buckets: dict[int, list[RawSensorFrameManifest]] = defaultdict(list)
        for f in sorted_frames:
            bucket_idx = (f.timestamp_us - base_us) // window_us
            buckets[bucket_idx].append(f)

        samples: list[SceneSampleManifest] = []
        for seq_idx, bucket_idx in enumerate(sorted(buckets)):
            bucket_frames = buckets[bucket_idx]
            sample_id = f"{scene_id}-sample-{seq_idx:06d}"
            ts = min(f.timestamp_us for f in bucket_frames)

            sensor_frames = [
                SceneSensorFrameManifest(
                    frame_id=f.frame_id,
                    sample_id=sample_id,
                    timestamp_us=f.timestamp_us,
                    channel=f.channel,
                    modality=f.modality,
                    uri=f.uri,
                    metadata=f.metadata,
                )
                for f in bucket_frames
            ]

            samples.append(
                SceneSampleManifest(
                    sample_id=sample_id,
                    scene_id=scene_id,
                    timestamp_us=ts,
                    frame_index=seq_idx,
                    sensor_frames=sensor_frames,
                )
            )

        return samples

    def _apply_missing_channel_policy(
        self,
        samples: list[SceneSampleManifest],
        *,
        report: SampleGroupingReport,
        scene_id: str,
    ) -> list[SceneSampleManifest]:
        """Filter or flag samples that are missing required channels.

        Mutates report in-place with drop/warn/missing counts, then sets
        report.sample_count_after_filtering to the number of retained samples.
        """
        required = set(self.config.required_channels)
        policy = self.config.missing_channel_policy
        retained: list[SceneSampleManifest] = []

        for sample in samples:
            present = {sf.channel for sf in sample.sensor_frames}
            missing = required - present

            if not missing:
                retained.append(sample)
                continue

            # Sample has at least one missing required channel.
            report.samples_with_missing_channels_count += 1
            for ch in sorted(missing):
                report.missing_channel_counts_by_channel[ch] = (
                    report.missing_channel_counts_by_channel.get(ch, 0) + 1
                )

            if policy == MissingChannelPolicy.KEEP_WITH_WARNING:
                retained.append(sample)
                report.warned_sample_count += 1
            elif policy == MissingChannelPolicy.DROP_SAMPLE:
                report.dropped_sample_count += 1
            elif policy == MissingChannelPolicy.FAIL_SCENE:
                raise ValueError(
                    f"Scene {scene_id!r}: sample {sample.sample_id!r} is missing "
                    f"required channels: {sorted(missing)}"
                )
            else:
                raise NotImplementedError(
                    f"Unsupported missing_channel_policy: {policy!r}"
                )

        report.sample_count_after_filtering = len(retained)
        return retained

    def _group_by_nearest_timestamp(
        self,
        frames: list[RawSensorFrameManifest],
        *,
        scene_id: str,
    ) -> list[SceneSampleManifest]:
        """Anchor each sample to a reference_channel frame"""
        if not frames:
            return []

        if not self.config.reference_channel:
            raise ValueError(
                "NEAREST_TIMESTAMP strategy requires reference_channel to be set in config"
            )

        tolerance_us = int(self.config.sync_tolerance_ms * 1000)
        sync_policy = self.config.sync_policy
        reference_channel = self.config.reference_channel

        by_channel: dict[str, list[RawSensorFrameManifest]] = defaultdict(list)
        for f in frames:
            by_channel[f.channel].append(f)
        for ch in by_channel:
            by_channel[ch].sort(key=lambda f: f.timestamp_us)

        ref_frames = by_channel.get(reference_channel, [])
        if not ref_frames:
            return []

        other_channels = sorted(ch for ch in by_channel if ch != reference_channel)

        samples: list[SceneSampleManifest] = []
        for idx, ref_frame in enumerate(ref_frames):
            ref_ts = ref_frame.timestamp_us
            selected: list[RawSensorFrameManifest] = [ref_frame]

            for ch in other_channels:
                nearest = self._find_nearest_frame(by_channel[ch], ref_ts)
                if nearest is None:
                    continue
                delta_us = abs(nearest.timestamp_us - ref_ts)
                if sync_policy == SensorSyncPolicy.EXACT and delta_us > 0:
                    continue
                if (
                    sync_policy == SensorSyncPolicy.WITHIN_TOLERANCE
                    and delta_us > tolerance_us
                ):
                    continue
                selected.append(nearest)

            sample_id = f"{scene_id}-sample-{idx:06d}"
            sensor_frames = [
                SceneSensorFrameManifest(
                    frame_id=f.frame_id,
                    sample_id=sample_id,
                    timestamp_us=f.timestamp_us,
                    channel=f.channel,
                    modality=f.modality,
                    uri=f.uri,
                    metadata=f.metadata,
                )
                for f in selected
            ]
            samples.append(
                SceneSampleManifest(
                    sample_id=sample_id,
                    scene_id=scene_id,
                    timestamp_us=ref_ts,
                    frame_index=idx,
                    sensor_frames=sensor_frames,
                )
            )

        return samples

    @staticmethod
    def _find_nearest_frame(
        sorted_frames: list[RawSensorFrameManifest],
        target_us: int,
    ) -> RawSensorFrameManifest | None:
        """Binary search for the frame whose timestamp is nearest to target_us."""
        if not sorted_frames:
            return None
        lo, hi = 0, len(sorted_frames) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if sorted_frames[mid].timestamp_us < target_us:
                lo = mid + 1
            else:
                hi = mid
        candidate = sorted_frames[lo]
        if lo > 0:
            prev = sorted_frames[lo - 1]
            if abs(prev.timestamp_us - target_us) < abs(
                candidate.timestamp_us - target_us
            ):
                candidate = prev
        return candidate
