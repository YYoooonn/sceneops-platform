"""Unit tests for SceneSegmenter (apps/worker/sceneops_worker/scenes/building/segmentation.py).

SceneOps V2 Request 10: replaces the deleted sceneops_worker.scenes.raw_scene_builder
module (free functions _segment_by_sequence/_segment_by_fixed_window/_segment_frames
operating on RawSensorFrameManifest.source_sequence_id/source_scene_id fallback
chains). The current RawSensorFrameManifest only has a single `sequence_id` field
(no fallback chain), and segmentation is a method on SceneSegmenter, not free
functions. SceneSegmentationStrategy also dropped its MANUAL member (SEQUENCE,
GAP_BASED, and FIXED_WINDOW are all handled), so the old "unsupported strategy
raises NotImplementedError" case is no longer reachable through the public enum.

Covers:
- SEQUENCE: groups by sequence_id, output sorted by start timestamp,
  min_frame_count filters small groups
- FIXED_WINDOW: true fixed buckets, -fw marker in segment_id, segment_id embeds
  the sequence id when respect_sequence_id=True (verified against real e2e
  output, e.g. "nuscenes-v1.0-mini-scene-0061-fw0000" — this is current,
  intentional behavior, not a bug)
- GAP_BASED: splits on timestamp gaps exceeding max_timestamp_gap_ms
- missing sequence_id: DEFAULT_SEGMENT vs DROP policy
"""

from __future__ import annotations

import pytest

from sceneops_core.observations.schemas.frames import RawSensorFrameManifest
from sceneops_core.scenes.schemas import MissingSequencePolicy
from sceneops_core.scenes.schemas.config import (
    SceneSegmentationConfig,
    SceneSegmentationStrategy,
)
from sceneops_core.sensors import SensorModality

from sceneops_worker.scenes.building.segmentation import SceneSegmenter


def _frame(
    frame_id: str,
    timestamp_us: int,
    channel: str = "CAM_FRONT",
    *,
    sequence_id: str | None = None,
) -> RawSensorFrameManifest:
    return RawSensorFrameManifest(
        frame_id=frame_id,
        timestamp_us=timestamp_us,
        channel=channel,
        modality=SensorModality.CAMERA,
        uri=f"s3://data/{frame_id}.jpg",
        sequence_id=sequence_id,
    )


_segmenter = SceneSegmenter()


def _segment(frames, config):
    return _segmenter.segment(
        frames=frames,
        config=config,
        raw_log_id="log",
        dataset_id="ds",
        dataset_version="v1",
    )


# ── SEQUENCE segmentation ───────────────────────────────────────────────────────


class TestSequenceSegmentation:
    def _config(self, min_frame_count: int = 1) -> SceneSegmentationConfig:
        return SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.SEQUENCE,
            min_frame_count=min_frame_count,
        )

    def test_groups_by_sequence_id(self) -> None:
        frames = [
            _frame(f"f{i:03d}", i * 100_000, sequence_id="seq-A") for i in range(5)
        ] + [_frame(f"g{i:03d}", i * 100_000, sequence_id="seq-B") for i in range(5)]

        segments = _segment(frames, self._config())

        segment_ids = {s.segment_id for s in segments}
        assert "seq-A" in segment_ids
        assert "seq-B" in segment_ids
        assert len(segments) == 2

    def test_output_sorted_by_start_timestamp(self) -> None:
        # seq-B starts earlier than seq-A
        frames = [
            _frame("fa0", 2_000_000, sequence_id="seq-A"),
            _frame("fa1", 3_000_000, sequence_id="seq-A"),
            _frame("fb0", 100_000, sequence_id="seq-B"),
            _frame("fb1", 200_000, sequence_id="seq-B"),
        ]

        segments = _segment(frames, self._config())

        assert segments[0].segment_id == "seq-B"
        assert segments[1].segment_id == "seq-A"

    def test_min_frame_count_filters_small_groups(self) -> None:
        frames = [
            _frame("f000", 0, sequence_id="big"),
            _frame("f001", 1, sequence_id="big"),
            _frame("f002", 2, sequence_id="big"),
            _frame("g000", 0, sequence_id="tiny"),
        ]

        segments = _segment(frames, self._config(min_frame_count=2))

        assert len(segments) == 1
        assert segments[0].segment_id == "big"

    def test_returns_empty_for_no_frames(self) -> None:
        assert _segment([], self._config()) == []

    def test_missing_sequence_id_uses_default_segment(self) -> None:
        frames = [_frame(f"f{i}", i * 10_000) for i in range(3)]  # no sequence_id
        config = SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.SEQUENCE,
            missing_sequence_policy=MissingSequencePolicy.DEFAULT_SEGMENT,
        )
        segments = _segment(frames, config)
        assert len(segments) == 1
        assert segments[0].segment_id == "default"

    def test_missing_sequence_id_dropped_when_policy_is_drop(self) -> None:
        frames = [_frame(f"f{i}", i * 10_000) for i in range(3)]  # no sequence_id
        config = SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.SEQUENCE,
            missing_sequence_policy=MissingSequencePolicy.DROP,
        )
        segments = _segment(frames, config)
        assert segments == []


# ── FIXED_WINDOW segmentation ────────────────────────────────────────────────────


class TestFixedWindowSegmentation:
    def _config(
        self, duration_ms: int = 2000, min_frame_count: int = 1
    ) -> SceneSegmentationConfig:
        return SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.FIXED_WINDOW,
            respect_sequence_id=False,
            fixed_window_duration_ms=duration_ms,
            min_frame_count=min_frame_count,
        )

    def test_exact_segment_count(self) -> None:
        # 20 frames at 500ms = 10 seconds -> 5 two-second windows
        frames = [_frame(f"f{i:03d}", i * 500_000) for i in range(20)]
        segments = _segment(frames, self._config(duration_ms=2000, min_frame_count=1))
        assert len(segments) == 5

    def test_segment_ids_contain_fw_marker(self) -> None:
        frames = [_frame(f"f{i:03d}", i * 500_000) for i in range(10)]
        segments = _segment(frames, self._config())
        for seg in segments:
            assert (
                "-fw" in seg.segment_id
            ), f"expected '-fw' in segment_id, got: {seg.segment_id}"

    def test_segment_id_embeds_sequence_id_when_respected(self) -> None:
        # Current (intentional) behavior differs from the old raw_scene_builder
        # module: with respect_sequence_id=True, fixed-window segment IDs embed
        # the sequence id, e.g. "log-ns-seq-abc-fw0000" — matches real e2e
        # output such as "nuscenes-v1.0-mini-scene-0061-fw0000".
        frames = [
            _frame(f"f{i:03d}", i * 500_000, sequence_id="ns-seq-abc")
            for i in range(10)
        ]
        config = SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.FIXED_WINDOW,
            respect_sequence_id=True,
            fixed_window_duration_ms=2000,
        )
        segments = _segment(frames, config)
        for seg in segments:
            assert "ns-seq-abc" in seg.segment_id

    def test_segments_have_distinct_timestamps(self) -> None:
        frames = [_frame(f"f{i:03d}", i * 500_000) for i in range(20)]
        segments = _segment(frames, self._config(duration_ms=2000))
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
        segments = _segment(frames, self._config(duration_ms=1000, min_frame_count=1))
        assert len(segments) == 2
        assert set(segments[0].frame_ids) == {"f0", "f1"}
        assert set(segments[1].frame_ids) == {"f2", "f3"}

    def test_returns_empty_for_no_frames(self) -> None:
        assert _segment([], self._config()) == []

    def test_min_frame_count_filters_sparse_buckets(self) -> None:
        # 1 frame per 3-second bucket, 2-second window -> filtered at min_frame_count=2
        frames = [_frame(f"f{i:03d}", i * 3_000_000) for i in range(5)]
        segments = _segment(frames, self._config(duration_ms=2000, min_frame_count=2))
        assert segments == []

    def test_invalid_duration_raises(self) -> None:
        # SceneSegmentationConfig validates this itself at construction time.
        with pytest.raises(ValueError, match="fixed_window_duration_ms"):
            SceneSegmentationConfig(
                strategy=SceneSegmentationStrategy.FIXED_WINDOW,
                respect_sequence_id=False,
                fixed_window_duration_ms=0,
            )

    def test_none_duration_raises(self) -> None:
        with pytest.raises(ValueError, match="fixed_window_duration_ms"):
            SceneSegmentationConfig(
                strategy=SceneSegmentationStrategy.FIXED_WINDOW,
                respect_sequence_id=False,
                fixed_window_duration_ms=None,
            )


# ── GAP_BASED segmentation ───────────────────────────────────────────────────────


class TestGapBasedSegmentation:
    def _config(
        self, gap_ms: int = 500, min_frame_count: int = 1
    ) -> SceneSegmentationConfig:
        return SceneSegmentationConfig(
            strategy=SceneSegmentationStrategy.GAP_BASED,
            respect_sequence_id=False,
            max_timestamp_gap_ms=gap_ms,
            min_frame_count=min_frame_count,
        )

    def test_splits_on_gap_exceeding_threshold(self) -> None:
        # Gap of 2s between f1 and f2 exceeds the 500ms threshold.
        frames = [
            _frame("f0", 0),
            _frame("f1", 100_000),
            _frame("f2", 2_100_000),
            _frame("f3", 2_200_000),
        ]
        segments = _segment(frames, self._config(gap_ms=500))
        assert len(segments) == 2
        assert set(segments[0].frame_ids) == {"f0", "f1"}
        assert set(segments[1].frame_ids) == {"f2", "f3"}

    def test_no_split_within_gap_threshold(self) -> None:
        frames = [_frame(f"f{i}", i * 100_000) for i in range(5)]
        segments = _segment(frames, self._config(gap_ms=500))
        assert len(segments) == 1

    def test_invalid_gap_raises(self) -> None:
        with pytest.raises(ValueError, match="max_timestamp_gap_ms"):
            SceneSegmentationConfig(
                strategy=SceneSegmentationStrategy.GAP_BASED,
                respect_sequence_id=False,
                max_timestamp_gap_ms=0,
            )
