from __future__ import annotations

from dataclasses import dataclass

from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.scenes.schemas import SampleGroupingConfig, SampleGroupingStrategy


@dataclass(frozen=True)
class SampleAnchor:
    sample_index: int
    timestamp_us: int
    anchor_frame_id: str | None = None
    anchor_channel: str | None = None


class SampleAnchorSelector:
    def select(
        self,
        *,
        frames: list[RawSensorFrameManifest],
        config: SampleGroupingConfig,
    ) -> list[SampleAnchor]:
        if not frames:
            return []

        sorted_frames = sorted(frames, key=lambda frame: frame.timestamp_us)

        if config.strategy == SampleGroupingStrategy.ANCHOR_CHANNEL:
            anchors = self._select_anchor_channel(
                frames=sorted_frames,
                config=config,
            )
        elif config.strategy == SampleGroupingStrategy.FIXED_INTERVAL:
            anchors = self._select_fixed_interval(
                frames=sorted_frames,
                config=config,
            )
        else:
            raise NotImplementedError(
                f"Unsupported sample grouping strategy: {config.strategy!r}"
            )

        anchors = self._apply_every_nth_anchor(
            anchors=anchors,
            every_nth_anchor=config.every_nth_anchor,
        )
        anchors = self._apply_min_sample_gap(
            anchors=anchors,
            min_sample_gap_ms=config.min_sample_gap_ms,
        )
        anchors = self._apply_max_samples(
            anchors=anchors,
            max_samples=config.max_samples,
        )

        return [
            SampleAnchor(
                sample_index=idx,
                timestamp_us=anchor.timestamp_us,
                anchor_frame_id=anchor.anchor_frame_id,
                anchor_channel=anchor.anchor_channel,
            )
            for idx, anchor in enumerate(anchors)
        ]

    @staticmethod
    def _select_anchor_channel(
        *,
        frames: list[RawSensorFrameManifest],
        config: SampleGroupingConfig,
    ) -> list[SampleAnchor]:
        anchor_frames = [
            frame for frame in frames if frame.channel == config.anchor_channel
        ]

        if not anchor_frames:
            anchor_frames = frames

        return [
            SampleAnchor(
                sample_index=idx,
                timestamp_us=frame.timestamp_us,
                anchor_frame_id=frame.frame_id,
                anchor_channel=frame.channel,
            )
            for idx, frame in enumerate(
                sorted(anchor_frames, key=lambda f: f.timestamp_us)
            )
        ]

    @staticmethod
    def _select_fixed_interval(
        *,
        frames: list[RawSensorFrameManifest],
        config: SampleGroupingConfig,
    ) -> list[SampleAnchor]:
        if not frames:
            return []

        if config.sample_interval_ms is None or config.sample_interval_ms <= 0:
            raise ValueError(
                "sample_interval_ms must be positive for fixed_interval sampling"
            )

        start_ts = frames[0].timestamp_us
        end_ts = frames[-1].timestamp_us
        interval_us = config.sample_interval_ms * 1000

        anchors: list[SampleAnchor] = []
        timestamp_us = start_ts
        idx = 0

        while timestamp_us <= end_ts:
            anchors.append(
                SampleAnchor(
                    sample_index=idx,
                    timestamp_us=timestamp_us,
                    anchor_frame_id=None,
                    anchor_channel=None,
                )
            )
            timestamp_us += interval_us
            idx += 1

        return anchors

    @staticmethod
    def _apply_every_nth_anchor(
        *,
        anchors: list[SampleAnchor],
        every_nth_anchor: int,
    ) -> list[SampleAnchor]:
        if every_nth_anchor <= 1:
            return anchors
        return anchors[::every_nth_anchor]

    @staticmethod
    def _apply_min_sample_gap(
        *,
        anchors: list[SampleAnchor],
        min_sample_gap_ms: int | None,
    ) -> list[SampleAnchor]:
        if min_sample_gap_ms is None:
            return anchors

        min_gap_us = min_sample_gap_ms * 1000
        kept: list[SampleAnchor] = []
        last_ts: int | None = None

        for anchor in anchors:
            if last_ts is None or anchor.timestamp_us - last_ts >= min_gap_us:
                kept.append(anchor)
                last_ts = anchor.timestamp_us

        return kept

    @staticmethod
    def _apply_max_samples(
        *,
        anchors: list[SampleAnchor],
        max_samples: int | None,
    ) -> list[SampleAnchor]:
        if max_samples is None:
            return anchors
        return anchors[:max_samples]
