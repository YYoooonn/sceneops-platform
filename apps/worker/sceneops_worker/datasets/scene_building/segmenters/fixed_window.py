from __future__ import annotations

import bisect
from collections import defaultdict

from sceneops_core.datasets.schemas import SceneBuildPolicy, SceneSegmentManifest

from sceneops_core.ids import generate_segment_id
from sceneops_worker.datasets.scene_building.models import IndexedRawFrame


class FixedWindowSceneSegmenter:
    def __init__(self, *, raw_log_id: str, policy: SceneBuildPolicy) -> None:
        self.raw_log_id = raw_log_id
        self.policy = policy

    def segment(self, frames: list[IndexedRawFrame]) -> list[SceneSegmentManifest]:
        if not frames:
            return []

        frames = sorted(frames, key=lambda item: item.timestamp_us)
        timestamps = [f.timestamp_us for f in frames]

        start_us = frames[0].timestamp_us
        last_us = frames[-1].timestamp_us

        window_us = int(self.policy.window_seconds * 1_000_000)
        stride_us = int(
            (self.policy.stride_seconds or self.policy.window_seconds) * 1_000_000
        )

        segments: list[SceneSegmentManifest] = []
        cursor_us = start_us

        while cursor_us <= last_us:
            end_us = cursor_us + window_us
            lo = bisect.bisect_left(timestamps, cursor_us)
            hi = bisect.bisect_left(timestamps, end_us)
            window_frames = frames[lo:hi]

            if self._is_valid(window_frames):
                channels = sorted({frame.channel for frame in window_frames})

                segments.append(
                    SceneSegmentManifest(
                        segment_id=generate_segment_id(),
                        raw_log_id=self.raw_log_id,
                        start_timestamp_us=cursor_us,
                        end_timestamp_us=end_us,
                        frame_ids=[frame.frame_id for frame in window_frames],
                        channels=channels,
                        policy=self.policy,
                        quality_summary=self._quality_summary(window_frames),
                    )
                )

            cursor_us += stride_us

        return segments

    def _is_valid(self, frames: list[IndexedRawFrame]) -> bool:
        if len(frames) < self.policy.min_frame_count:
            return False

        channels = {frame.channel for frame in frames}
        missing = set(self.policy.required_channels) - channels

        if self.policy.split_on_missing_required_channel and missing:
            return False

        return True

    def _quality_summary(self, frames: list[IndexedRawFrame]) -> dict[str, object]:
        by_channel: dict[str, list[int]] = defaultdict(list)

        for frame in frames:
            by_channel[frame.channel].append(frame.timestamp_us)

        max_gap_ms = 0
        frame_count_by_channel: dict[str, int] = {}

        for channel, timestamps in by_channel.items():
            timestamps = sorted(timestamps)
            frame_count_by_channel[channel] = len(timestamps)

            for prev, curr in zip(timestamps, timestamps[1:], strict=False):
                max_gap_ms = max(max_gap_ms, int((curr - prev) / 1000))

        return {
            "frame_count": len(frames),
            "frame_count_by_channel": frame_count_by_channel,
            "missing_required_channels": sorted(
                set(self.policy.required_channels) - set(by_channel)
            ),
            "max_timestamp_gap_ms": max_gap_ms,
            "is_timestamp_gap_within_policy": max_gap_ms
            <= self.policy.max_timestamp_gap_ms,
        }
