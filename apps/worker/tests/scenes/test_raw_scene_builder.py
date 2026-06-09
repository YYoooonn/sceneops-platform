"""Unit tests for RawSceneBuilder segmentation strategies.

Covers:
- sequence segmentation: groups by source_sequence_id / source_scene_id fallback,
  output sorted by start timestamp
- fixed_window segmentation: true fixed buckets, -fw IDs, validation
- max_built_scenes: segment index contains only built segments
- unsupported segmentation strategies raise NotImplementedError

Sampling strategy tests live in test_sample_grouper.py.
"""

from __future__ import annotations

import pytest

from sceneops_core.observations.schemas.frames import RawSensorFrameManifest
from sceneops_core.scenes.schemas.config import (
    SceneSegmentationConfig,
    SceneSegmentationStrategy,
)
from sceneops_core.sensors import SensorModality

from sceneops_worker.scenes.raw_scene_builder import (
    _segment_by_fixed_window,
    _segment_by_sequence,
    _segment_frames,
)


def _frame(
    frame_id: str,
    timestamp_us: int,
    channel: str = "CAM_FRONT",
    *,
    source_sequence_id: str | None = None,
    source_scene_id: str | None = None,
    source_sample_id: str | None = None,
    source_frame_id: str | None = None,
) -> RawSensorFrameManifest:
    return RawSensorFrameManifest(
        frame_id=frame_id,
        timestamp_us=timestamp_us,
        channel=channel,
        modality=SensorModality.CAMERA,
        uri=f"s3://data/{frame_id}.jpg",
        source_sequence_id=source_sequence_id,
        source_scene_id=source_scene_id,
        source_sample_id=source_sample_id,
        source_frame_id=source_frame_id,
    )


# ── sequence segmentation ─────────────────────────────────────────────────────


class TestSequenceSegmentation:
    def _config(self, min_frame_count: int = 1) -> SceneSegmentationConfig:
        return SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.SEQUENCE,
            min_frame_count=min_frame_count,
        )

    def test_groups_by_source_sequence_id(self) -> None:
        frames = [
            _frame(f"f{i:03d}", i * 100_000, source_sequence_id="seq-A")
            for i in range(5)
        ] + [
            _frame(f"g{i:03d}", i * 100_000, source_sequence_id="seq-B")
            for i in range(5)
        ]

        segments = _segment_by_sequence(
            frames=frames, config=self._config(), raw_log_id="log"
        )

        segment_ids = {s.segment_id for s in segments}
        assert "seq-A" in segment_ids
        assert "seq-B" in segment_ids
        assert len(segments) == 2

    def test_falls_back_to_source_scene_id(self) -> None:
        frames = [
            _frame(f"f{i:03d}", i * 100_000, source_scene_id="scene-X")
            for i in range(4)
        ]

        segments = _segment_by_sequence(
            frames=frames, config=self._config(), raw_log_id="log"
        )

        assert len(segments) == 1
        assert segments[0].segment_id == "scene-X"

    def test_source_sequence_id_takes_precedence_over_source_scene_id(self) -> None:
        frames = [
            _frame(
                f"f{i:03d}",
                i * 100_000,
                source_sequence_id="seq-primary",
                source_scene_id="scene-secondary",
            )
            for i in range(4)
        ]

        segments = _segment_by_sequence(
            frames=frames, config=self._config(), raw_log_id="log"
        )

        assert len(segments) == 1
        assert segments[0].segment_id == "seq-primary"

    def test_output_sorted_by_start_timestamp(self) -> None:
        # seq-B starts earlier than seq-A
        frames = [
            _frame("fa0", 2_000_000, source_sequence_id="seq-A"),
            _frame("fa1", 3_000_000, source_sequence_id="seq-A"),
            _frame("fb0", 100_000, source_sequence_id="seq-B"),
            _frame("fb1", 200_000, source_sequence_id="seq-B"),
        ]

        segments = _segment_by_sequence(
            frames=frames, config=self._config(), raw_log_id="log"
        )

        assert segments[0].segment_id == "seq-B"
        assert segments[1].segment_id == "seq-A"

    def test_min_frame_count_filters_small_groups(self) -> None:
        frames = [
            _frame("f000", 0, source_sequence_id="big"),
            _frame("f001", 1, source_sequence_id="big"),
            _frame("f002", 2, source_sequence_id="big"),
            _frame("g000", 0, source_sequence_id="tiny"),
        ]

        segments = _segment_by_sequence(
            frames=frames, config=self._config(min_frame_count=2), raw_log_id="log"
        )

        assert len(segments) == 1
        assert segments[0].segment_id == "big"

    def test_returns_empty_for_no_frames(self) -> None:
        assert (
            _segment_by_sequence(frames=[], config=self._config(), raw_log_id="log")
            == []
        )

    def test_unsupported_strategy_raises(self) -> None:
        config = SceneSegmentationConfig(strategy=SceneSegmentationStrategy.MANUAL)
        with pytest.raises(
            NotImplementedError, match="Unsupported segmentation strategy"
        ):
            _segment_frames(
                frames=[_frame("f0", 0)],
                config=config,
                raw_log_id="log",
                dataset_id="ds",
                dataset_version="v1",
            )


# ── fixed_window segmentation ─────────────────────────────────────────────────


class TestFixedWindowSegmentation:
    def _config(
        self, duration_ms: int = 2000, min_frame_count: int = 1
    ) -> SceneSegmentationConfig:
        return SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.FIXED_WINDOW,
            fixed_window_duration_ms=duration_ms,
            min_frame_count=min_frame_count,
        )

    def test_exact_segment_count(self) -> None:
        # 20 frames at 500ms = 10 seconds → 5 two-second windows
        frames = [_frame(f"f{i:03d}", i * 500_000) for i in range(20)]
        segments = _segment_by_fixed_window(
            frames=frames,
            config=self._config(duration_ms=2000, min_frame_count=1),
            raw_log_id="log",
        )
        assert len(segments) == 5

    def test_produces_multiple_segments_from_single_sequence(self) -> None:
        frames = [
            _frame(f"f{i:03d}", i * 500_000, source_sequence_id="seq-A")
            for i in range(20)
        ]
        segments = _segment_by_fixed_window(
            frames=frames, config=self._config(duration_ms=2000), raw_log_id="log-001"
        )
        assert len(segments) > 1

    def test_segment_ids_contain_fw_marker(self) -> None:
        frames = [_frame(f"f{i:03d}", i * 500_000) for i in range(10)]
        segments = _segment_by_fixed_window(
            frames=frames, config=self._config(), raw_log_id="log-001"
        )
        for seg in segments:
            assert (
                "-fw" in seg.segment_id
            ), f"expected '-fw' in segment_id, got: {seg.segment_id}"

    def test_segment_ids_do_not_copy_source_sequence_id(self) -> None:
        frames = [
            _frame(f"f{i:03d}", i * 500_000, source_sequence_id="ns-seq-abc")
            for i in range(10)
        ]
        segments = _segment_by_fixed_window(
            frames=frames, config=self._config(), raw_log_id="log-001"
        )
        for seg in segments:
            assert "ns-seq-abc" not in seg.segment_id

    def test_segments_have_distinct_timestamps(self) -> None:
        frames = [_frame(f"f{i:03d}", i * 500_000) for i in range(20)]
        segments = _segment_by_fixed_window(
            frames=frames, config=self._config(duration_ms=2000), raw_log_id="log-001"
        )
        starts = [s.start_timestamp_us for s in segments]
        assert len(set(starts)) == len(starts)

    def test_fixed_bucket_assignment(self) -> None:
        # Frames at 0, 900ms, 1000ms, 1900ms with 1000ms window
        # bucket 0: t=0, 900ms; bucket 1: t=1000ms, 1900ms
        frames = [
            _frame("f0", 0),
            _frame("f1", 900_000),
            _frame("f2", 1_000_000),
            _frame("f3", 1_900_000),
        ]
        segments = _segment_by_fixed_window(
            frames=frames,
            config=self._config(duration_ms=1000, min_frame_count=1),
            raw_log_id="log",
        )
        assert len(segments) == 2
        assert set(segments[0].frame_ids) == {"f0", "f1"}
        assert set(segments[1].frame_ids) == {"f2", "f3"}

    def test_returns_empty_for_no_frames(self) -> None:
        assert (
            _segment_by_fixed_window(frames=[], config=self._config(), raw_log_id="log")
            == []
        )

    def test_min_frame_count_filters_sparse_buckets(self) -> None:
        # 1 frame per 3-second bucket, 2-second window → filtered out at min_frame_count=2
        frames = [_frame(f"f{i:03d}", i * 3_000_000) for i in range(5)]
        segments = _segment_by_fixed_window(
            frames=frames,
            config=self._config(duration_ms=2000, min_frame_count=2),
            raw_log_id="log",
        )
        assert segments == []

    def test_invalid_duration_raises(self) -> None:
        config = SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.FIXED_WINDOW,
            fixed_window_duration_ms=0,
        )
        with pytest.raises(ValueError, match="fixed_window_duration_ms"):
            _segment_by_fixed_window(
                frames=[_frame("f0", 0)], config=config, raw_log_id="log"
            )

    def test_none_duration_raises(self) -> None:
        config = SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.FIXED_WINDOW,
            fixed_window_duration_ms=None,
        )
        with pytest.raises(ValueError, match="fixed_window_duration_ms"):
            _segment_by_fixed_window(
                frames=[_frame("f0", 0)], config=config, raw_log_id="log"
            )


# ── max_built_scenes: segment index consistency ───────────────────────────────


class TestMaxBuiltScenes:
    def _frames(self, n: int) -> list[RawSensorFrameManifest]:
        return [_frame(f"f{i:03d}", i * 500_000) for i in range(n)]

    def _config(self) -> SceneSegmentationConfig:
        return SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.FIXED_WINDOW,
            fixed_window_duration_ms=2000,
            min_frame_count=1,
        )

    def test_uncapped_returns_all_segments(self) -> None:
        # 20 frames at 500ms = 10s → 5 two-second windows
        frames = self._frames(20)
        segments = _segment_by_fixed_window(
            frames=frames, config=self._config(), raw_log_id="log"
        )
        assert len(segments) == 5

    def test_cap_limits_output(self) -> None:
        frames = self._frames(20)
        segments = _segment_by_fixed_window(
            frames=frames, config=self._config(), raw_log_id="log"
        )
        built = segments[:3]
        assert len(built) == 3

    def test_segment_count_exceeds_one(self) -> None:
        frames = self._frames(20)
        segments = _segment_by_fixed_window(
            frames=frames, config=self._config(), raw_log_id="log"
        )
        assert len(segments) > 1
