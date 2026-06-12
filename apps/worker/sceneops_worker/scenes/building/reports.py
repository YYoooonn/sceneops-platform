from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from sceneops_core.common.schemas import JsonDict


@dataclass
class SampleGroupingReport:
    sample_count_before_filtering: int = 0
    sample_count_after_filtering: int = 0
    dropped_sample_count: int = 0

    warned_sample_count: int = 0
    samples_with_missing_channels_count: int = 0
    missing_channel_counts_by_channel: dict[str, int] = field(default_factory=dict)

    associated_frame_count: int = 0
    frames_without_calibration_count: int = 0
    frames_without_ego_pose_count: int = 0
    samples_with_missing_calibration_count: int = 0
    samples_with_missing_ego_pose_count: int = 0

    def merge(self, other: "SampleGroupingReport") -> "SampleGroupingReport":
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

        self.associated_frame_count += other.associated_frame_count
        self.frames_without_calibration_count += other.frames_without_calibration_count
        self.frames_without_ego_pose_count += other.frames_without_ego_pose_count
        self.samples_with_missing_calibration_count += (
            other.samples_with_missing_calibration_count
        )
        self.samples_with_missing_ego_pose_count += (
            other.samples_with_missing_ego_pose_count
        )

        return self

    @classmethod
    def from_associated_samples(
        cls,
        associated_samples: Iterable[Any],
        kept_sample_ids: set[str] | None = None,
    ) -> "SampleGroupingReport":
        samples = list(associated_samples)

        if kept_sample_ids is None:
            kept_sample_ids = {sample.sample_id for sample in samples if sample.frames}

        kept_samples = [
            sample for sample in samples if sample.sample_id in kept_sample_ids
        ]

        samples_with_missing_channels = [
            sample for sample in samples if sample.missing_channels
        ]

        missing_channel_counts: dict[str, int] = {}
        for sample in samples_with_missing_channels:
            for channel in sample.missing_channels:
                missing_channel_counts[channel] = (
                    missing_channel_counts.get(channel, 0) + 1
                )

        return cls(
            sample_count_before_filtering=len(samples),
            sample_count_after_filtering=len(kept_samples),
            dropped_sample_count=len(samples) - len(kept_samples),
            warned_sample_count=len(samples_with_missing_channels),
            samples_with_missing_channels_count=len(samples_with_missing_channels),
            missing_channel_counts_by_channel=missing_channel_counts,
            associated_frame_count=sum(len(sample.frames) for sample in kept_samples),
        )

    def add_resolution_stats(
        self,
        *,
        samples_with_missing_calibration_count: int = 0,
        samples_with_missing_ego_pose_count: int = 0,
        frames_without_calibration_count: int = 0,
        frames_without_ego_pose_count: int = 0,
    ) -> "SampleGroupingReport":
        self.samples_with_missing_calibration_count += (
            samples_with_missing_calibration_count
        )
        self.samples_with_missing_ego_pose_count += samples_with_missing_ego_pose_count
        self.frames_without_calibration_count += frames_without_calibration_count
        self.frames_without_ego_pose_count += frames_without_ego_pose_count

        return self

    def to_metadata(self) -> JsonDict:
        return {
            "sample_count_before_filtering": self.sample_count_before_filtering,
            "sample_count_after_filtering": self.sample_count_after_filtering,
            "dropped_sample_count": self.dropped_sample_count,
            "warned_sample_count": self.warned_sample_count,
            "samples_with_missing_channels_count": (
                self.samples_with_missing_channels_count
            ),
            "missing_channel_counts_by_channel": dict(
                self.missing_channel_counts_by_channel
            ),
            "associated_frame_count": self.associated_frame_count,
            "frames_without_calibration_count": (self.frames_without_calibration_count),
            "frames_without_ego_pose_count": self.frames_without_ego_pose_count,
            "samples_with_missing_calibration_count": (
                self.samples_with_missing_calibration_count
            ),
            "samples_with_missing_ego_pose_count": (
                self.samples_with_missing_ego_pose_count
            ),
        }
