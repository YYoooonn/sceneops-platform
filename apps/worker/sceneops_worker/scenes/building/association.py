from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.scenes.schemas import (
    FrameAssociationStrategy,
    SampleGroupingConfig,
)

from .sampling import SampleAnchor


@dataclass(frozen=True)
class AssociatedSample:
    sample_id: str
    scene_id: str
    timestamp_us: int
    frame_index: int
    anchor: SampleAnchor
    frames: list[RawSensorFrameManifest] = field(default_factory=list)
    missing_channels: list[str] = field(default_factory=list)


class FrameAssociator:
    def associate(
        self,
        *,
        scene_id: str,
        frames: list[RawSensorFrameManifest],
        anchors: list[SampleAnchor],
        config: SampleGroupingConfig,
    ) -> list[AssociatedSample]:
        if not frames or not anchors:
            return []

        frames_by_channel = self._group_by_channel(frames)
        target_channels = self._target_channels(frames=frames, config=config)
        tolerance_us = config.association_tolerance_ms * 1000

        used_frame_ids: set[str] = set()
        associated_samples: list[AssociatedSample] = []

        for anchor in anchors:
            sample_id = f"{scene_id}-sample-{anchor.sample_index:06d}"

            selected_frames: list[RawSensorFrameManifest] = []
            missing_channels: list[str] = []

            for channel in target_channels:
                candidates = frames_by_channel.get(channel, [])

                if not config.allow_frame_reuse:
                    candidates = [
                        frame
                        for frame in candidates
                        if frame.frame_id not in used_frame_ids
                    ]

                selected = self._select_frame(
                    frames=candidates,
                    timestamp_us=anchor.timestamp_us,
                    tolerance_us=tolerance_us,
                    strategy=config.association_strategy,
                )

                if selected is None:
                    missing_channels.append(channel)
                    continue

                selected_frames.append(selected)
                used_frame_ids.add(selected.frame_id)

            selected_frames.sort(key=lambda frame: (frame.timestamp_us, frame.channel))

            associated_samples.append(
                AssociatedSample(
                    sample_id=sample_id,
                    scene_id=scene_id,
                    timestamp_us=anchor.timestamp_us,
                    frame_index=anchor.sample_index,
                    anchor=anchor,
                    frames=selected_frames,
                    missing_channels=missing_channels,
                )
            )

        return associated_samples

    @staticmethod
    def _group_by_channel(
        frames: list[RawSensorFrameManifest],
    ) -> dict[str, list[RawSensorFrameManifest]]:
        grouped: dict[str, list[RawSensorFrameManifest]] = defaultdict(list)

        for frame in frames:
            grouped[frame.channel].append(frame)

        return {
            channel: sorted(channel_frames, key=lambda frame: frame.timestamp_us)
            for channel, channel_frames in grouped.items()
        }

    @staticmethod
    def _target_channels(
        *,
        frames: list[RawSensorFrameManifest],
        config: SampleGroupingConfig,
    ) -> list[str]:
        if config.required_channels:
            return sorted(config.required_channels)

        return sorted({frame.channel for frame in frames})

    def _select_frame(
        self,
        *,
        frames: list[RawSensorFrameManifest],
        timestamp_us: int,
        tolerance_us: int,
        strategy: FrameAssociationStrategy,
    ) -> RawSensorFrameManifest | None:
        if strategy == FrameAssociationStrategy.NEAREST:
            return self._nearest_frame(
                frames=frames,
                timestamp_us=timestamp_us,
                tolerance_us=tolerance_us,
            )

        if strategy == FrameAssociationStrategy.PREVIOUS:
            return self._previous_frame(
                frames=frames,
                timestamp_us=timestamp_us,
                tolerance_us=tolerance_us,
            )

        if strategy == FrameAssociationStrategy.NEXT:
            return self._next_frame(
                frames=frames,
                timestamp_us=timestamp_us,
                tolerance_us=tolerance_us,
            )

        raise NotImplementedError(f"Unsupported association strategy: {strategy!r}")

    @staticmethod
    def _nearest_frame(
        *,
        frames: list[RawSensorFrameManifest],
        timestamp_us: int,
        tolerance_us: int,
    ) -> RawSensorFrameManifest | None:
        if not frames:
            return None

        nearest = min(
            frames,
            key=lambda frame: abs(frame.timestamp_us - timestamp_us),
        )

        delta_us = abs(nearest.timestamp_us - timestamp_us)
        if delta_us > tolerance_us:
            return None

        return nearest

    @staticmethod
    def _previous_frame(
        *,
        frames: list[RawSensorFrameManifest],
        timestamp_us: int,
        tolerance_us: int,
    ) -> RawSensorFrameManifest | None:
        candidates = [frame for frame in frames if frame.timestamp_us <= timestamp_us]

        if not candidates:
            return None

        selected = max(candidates, key=lambda frame: frame.timestamp_us)
        delta_us = timestamp_us - selected.timestamp_us

        if delta_us > tolerance_us:
            return None

        return selected

    @staticmethod
    def _next_frame(
        *,
        frames: list[RawSensorFrameManifest],
        timestamp_us: int,
        tolerance_us: int,
    ) -> RawSensorFrameManifest | None:
        candidates = [frame for frame in frames if frame.timestamp_us >= timestamp_us]

        if not candidates:
            return None

        selected = min(candidates, key=lambda frame: frame.timestamp_us)
        delta_us = selected.timestamp_us - timestamp_us

        if delta_us > tolerance_us:
            return None

        return selected
