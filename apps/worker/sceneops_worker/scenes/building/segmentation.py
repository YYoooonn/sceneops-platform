from __future__ import annotations

from collections import defaultdict

from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.scenes.schemas import (
    MissingSequencePolicy,
    SceneSegmentationConfig,
    SceneSegmentationStrategy,
)
from sceneops_core.scenes.schemas.segments import SceneSegment


class SceneSegmenter:
    """Split raw sensor frames into scene segments"""

    def segment(
        self,
        *,
        frames: list[RawSensorFrameManifest],
        config: SceneSegmentationConfig,
        raw_log_id: str,
        dataset_id: str,
        dataset_version: str,
    ) -> list[SceneSegment]:
        if not frames:
            return []

        grouped_frames = self._group_frames(
            frames=frames,
            config=config,
            raw_log_id=raw_log_id,
        )

        segments: list[SceneSegment] = []

        for group_key in sorted(grouped_frames):
            group = sorted(grouped_frames[group_key], key=lambda f: f.timestamp_us)

            if config.strategy == SceneSegmentationStrategy.SEQUENCE:
                candidate_chunks = [group]
                segment_kind = "seq"
            elif config.strategy == SceneSegmentationStrategy.GAP_BASED:
                candidate_chunks = self._split_by_gap(
                    frames=group,
                    config=config,
                )
                segment_kind = "gap"
            elif config.strategy == SceneSegmentationStrategy.FIXED_WINDOW:
                candidate_chunks = self._split_by_fixed_window(
                    frames=group,
                    config=config,
                )
                segment_kind = "fw"
            else:
                raise NotImplementedError(
                    f"Unsupported segmentation strategy: {config.strategy!r}"
                )

            chunk_idx = 0
            for chunk in candidate_chunks:
                for limited_chunk in self._split_by_limits(
                    frames=chunk,
                    config=config,
                ):
                    if not self._passes_filters(
                        frames=limited_chunk,
                        config=config,
                    ):
                        continue

                    segment_id = self._make_segment_id(
                        raw_log_id=raw_log_id,
                        group_key=group_key,
                        strategy=config.strategy,
                        segment_kind=segment_kind,
                        index=chunk_idx,
                    )

                    segments.append(
                        self._make_segment(
                            segment_id=segment_id,
                            frames=limited_chunk,
                            raw_log_id=raw_log_id,
                            config=config,
                        )
                    )
                    chunk_idx += 1

        segments.sort(key=lambda segment: segment.start_timestamp_us)
        return segments

    @staticmethod
    def _group_frames(
        *,
        frames: list[RawSensorFrameManifest],
        config: SceneSegmentationConfig,
        raw_log_id: str,
    ) -> dict[str, list[RawSensorFrameManifest]]:
        if not config.respect_sequence_id:
            return {raw_log_id: list(frames)}

        grouped: dict[str, list[RawSensorFrameManifest]] = defaultdict(list)

        for frame in frames:
            sequence_id = frame.sequence_id

            if not sequence_id:
                if config.missing_sequence_policy == MissingSequencePolicy.DROP:
                    continue
                sequence_id = config.default_sequence_id

            grouped[sequence_id].append(frame)

        return dict(grouped)

    @staticmethod
    def _split_by_gap(
        *,
        frames: list[RawSensorFrameManifest],
        config: SceneSegmentationConfig,
    ) -> list[list[RawSensorFrameManifest]]:
        if not frames:
            return []

        if config.max_timestamp_gap_ms is None or config.max_timestamp_gap_ms <= 0:
            raise ValueError(
                "max_timestamp_gap_ms must be positive for gap_based segmentation"
            )

        max_gap_us = config.max_timestamp_gap_ms * 1000

        chunks: list[list[RawSensorFrameManifest]] = []
        current: list[RawSensorFrameManifest] = [frames[0]]

        for frame in frames[1:]:
            gap_us = frame.timestamp_us - current[-1].timestamp_us

            if gap_us > max_gap_us:
                chunks.append(current)
                current = [frame]
            else:
                current.append(frame)

        chunks.append(current)
        return chunks

    @staticmethod
    def _split_by_fixed_window(
        *,
        frames: list[RawSensorFrameManifest],
        config: SceneSegmentationConfig,
    ) -> list[list[RawSensorFrameManifest]]:
        if not frames:
            return []

        if (
            config.fixed_window_duration_ms is None
            or config.fixed_window_duration_ms <= 0
        ):
            raise ValueError(
                "fixed_window_duration_ms must be positive for fixed_window "
                "segmentation"
            )

        duration_us = config.fixed_window_duration_ms * 1000
        stride_us = (
            config.fixed_window_stride_ms * 1000
            if config.fixed_window_stride_ms is not None
            else duration_us
        )

        start_ts = frames[0].timestamp_us
        end_ts = frames[-1].timestamp_us

        chunks: list[list[RawSensorFrameManifest]] = []
        window_start = start_ts

        while window_start <= end_ts:
            window_end = window_start + duration_us

            chunk = [
                frame
                for frame in frames
                if window_start <= frame.timestamp_us < window_end
            ]

            if chunk:
                chunks.append(chunk)

            window_start += stride_us

        return chunks

    @staticmethod
    def _split_by_limits(
        *,
        frames: list[RawSensorFrameManifest],
        config: SceneSegmentationConfig,
    ) -> list[list[RawSensorFrameManifest]]:
        """Split a candidate chunk by max_duration_ms and max_frame_count.

        This is applied after sequence/gap/window segmentation. It prevents very
        long segments from becoming too large for downstream scene manifests.
        """

        if not frames:
            return []

        if config.max_duration_ms is None and config.max_frame_count is None:
            return [frames]

        max_duration_us = (
            config.max_duration_ms * 1000
            if config.max_duration_ms is not None
            else None
        )
        max_frame_count = config.max_frame_count

        chunks: list[list[RawSensorFrameManifest]] = []
        current: list[RawSensorFrameManifest] = []

        for frame in frames:
            if not current:
                current = [frame]
                continue

            would_exceed_frame_count = (
                max_frame_count is not None and len(current) + 1 > max_frame_count
            )
            would_exceed_duration = (
                max_duration_us is not None
                and frame.timestamp_us - current[0].timestamp_us > max_duration_us
            )

            if would_exceed_frame_count or would_exceed_duration:
                chunks.append(current)
                current = [frame]
            else:
                current.append(frame)

        if current:
            chunks.append(current)

        return chunks

    @staticmethod
    def _passes_filters(
        *,
        frames: list[RawSensorFrameManifest],
        config: SceneSegmentationConfig,
    ) -> bool:
        if len(frames) < config.min_frame_count:
            return False

        if config.min_duration_ms is not None:
            duration_us = frames[-1].timestamp_us - frames[0].timestamp_us
            if duration_us < config.min_duration_ms * 1000:
                return False

        return True

    @staticmethod
    def _make_segment_id(
        *,
        raw_log_id: str,
        group_key: str,
        strategy: SceneSegmentationStrategy,
        segment_kind: str,
        index: int,
    ) -> str:
        # For sequence segmentation, keep the source sequence id as scene id when
        # possible. This keeps nuScenes mock scenes readable, e.g. "scene-0061".
        if strategy == SceneSegmentationStrategy.SEQUENCE and index == 0:
            return group_key

        safe_group_key = group_key.replace("/", "_").replace(" ", "_")
        return f"{raw_log_id}-{safe_group_key}-{segment_kind}{index:04d}"

    @staticmethod
    def _make_segment(
        *,
        segment_id: str,
        frames: list[RawSensorFrameManifest],
        raw_log_id: str,
        config: SceneSegmentationConfig,
    ) -> SceneSegment:
        sorted_frames = sorted(frames, key=lambda frame: frame.timestamp_us)

        return SceneSegment(
            segment_id=segment_id,
            raw_log_id=raw_log_id,
            start_timestamp_us=sorted_frames[0].timestamp_us,
            end_timestamp_us=sorted_frames[-1].timestamp_us,
            frame_ids=[frame.frame_id for frame in sorted_frames],
            channels=sorted({frame.channel for frame in sorted_frames}),
            segmentation=config,
        )
