"""Unit tests for SampleGrouper — Phase 1/2/3/4.

Covers:
- FRAME_ID strategy: grouping, fallback key resolution, sample_id format, ordering
- TIME_BUCKET strategy: fixed buckets, sample_id format, edge cases, validation
- NEAREST_TIMESTAMP strategy: reference-channel anchoring, sync_policy, edge cases
- SampleGroupingReport: counts match samples, all filter counts are zero
- Empty input: returns empty samples and zero-valued report
"""

from __future__ import annotations

import pytest

from sceneops_core.observations.schemas.frames import RawSensorFrameManifest
from sceneops_core.scenes.schemas.sampling import (
    MissingChannelPolicy,
    SampleGroupingConfig,
    SampleGroupingStrategy,
    SensorSyncPolicy,
)
from sceneops_core.sensors import SensorModality

from sceneops_worker.scenes.sample_grouping import SampleGrouper, SampleGroupingReport


def _frame(
    frame_id: str,
    timestamp_us: int,
    channel: str = "CAM_FRONT",
    *,
    source_sample_id: str | None = None,
    source_frame_id: str | None = None,
) -> RawSensorFrameManifest:
    return RawSensorFrameManifest(
        frame_id=frame_id,
        timestamp_us=timestamp_us,
        channel=channel,
        modality=SensorModality.CAMERA,
        uri=f"s3://data/{frame_id}.jpg",
        source_sample_id=source_sample_id,
        source_frame_id=source_frame_id,
    )


def _frame_id_grouper(
    sample_time_window_ms: float | None = None,
) -> SampleGrouper:
    return SampleGrouper(SampleGroupingConfig(strategy=SampleGroupingStrategy.FRAME_ID))


def _time_bucket_grouper(window_ms: float = 500.0) -> SampleGrouper:
    return SampleGrouper(
        SampleGroupingConfig(
            strategy=SampleGroupingStrategy.TIME_BUCKET,
            sample_time_window_ms=window_ms,
        )
    )


# ── FRAME_ID ──────────────────────────────────────────────────────────────────


class TestFrameIdGrouper:
    def test_group_by_frame_id_basic(self) -> None:
        frames = [
            _frame(f"fa{i}", i * 10_000, source_frame_id="grp-A") for i in range(3)
        ] + [_frame(f"fb{i}", i * 10_000, source_frame_id="grp-B") for i in range(2)]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc-001")
        assert len(samples) == 2

    def test_sample_id_format_uses_sample_marker(self) -> None:
        frames = [
            _frame(f"f{i}", i * 10_000, source_frame_id=f"grp-{i}") for i in range(4)
        ]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc-001")
        for s in samples:
            assert "-sample-" in s.sample_id, f"expected '-sample-' in {s.sample_id!r}"

    def test_sample_id_format_is_six_digit_padded(self) -> None:
        frames = [
            _frame("f0", 0, source_frame_id="g0"),
            _frame("f1", 1, source_frame_id="g1"),
        ]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc")
        assert samples[0].sample_id == "sc-sample-000000"
        assert samples[1].sample_id == "sc-sample-000001"

    def test_group_by_frame_id_ordering(self) -> None:
        # grp-B has lower min timestamp than grp-A — must appear first
        frames = [
            _frame("fa0", 1_000_000, source_frame_id="grp-A"),
            _frame("fa1", 2_000_000, source_frame_id="grp-A"),
            _frame("fb0", 100_000, source_frame_id="grp-B"),
            _frame("fb1", 200_000, source_frame_id="grp-B"),
        ]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc")
        assert samples[0].timestamp_us == 100_000
        assert samples[1].timestamp_us == 1_000_000

    def test_falls_back_to_source_sample_id(self) -> None:
        frames = [
            _frame(f"f{i}", i * 10_000, source_sample_id="samp-1") for i in range(3)
        ]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc-001")
        assert len(samples) == 1

    def test_source_frame_id_takes_precedence_over_source_sample_id(self) -> None:
        frames = [
            _frame("f0", 0, source_frame_id="by-frame", source_sample_id="by-sample"),
            _frame("f1", 1, source_frame_id="by-frame", source_sample_id="by-sample"),
        ]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc")
        assert len(samples) == 1

    def test_sample_ids_do_not_copy_source_frame_id(self) -> None:
        source_ids = {f"sfid-{i}" for i in range(4)}
        frames = [
            _frame(f"f{i}", i * 10_000, source_frame_id=f"sfid-{i}") for i in range(4)
        ]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc-001")
        for s in samples:
            assert s.sample_id not in source_ids

    def test_frame_index_increments_sequentially(self) -> None:
        frames = [
            _frame(f"f{i}", i * 10_000, source_frame_id=f"g{i}") for i in range(5)
        ]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc")
        for idx, s in enumerate(samples):
            assert s.frame_index == idx

    def test_sensor_frames_carry_correct_metadata(self) -> None:
        frames = [_frame("f0", 1000, "LIDAR_TOP", source_frame_id="grp")]
        samples, _ = _frame_id_grouper().group(frames, scene_id="sc")
        sf = samples[0].sensor_frames[0]
        assert sf.frame_id == "f0"
        assert sf.channel == "LIDAR_TOP"
        assert sf.timestamp_us == 1000
        assert sf.uri == "s3://data/f0.jpg"


# ── TIME_BUCKET ───────────────────────────────────────────────────────────────


class TestTimeBucketGrouper:
    def test_group_by_time_bucket_basic(self) -> None:
        # bucket 0: t=0, 400ms  |  bucket 1: t=1000ms, 1400ms
        frames = [
            _frame("f0", 0),
            _frame("f1", 400_000),
            _frame("f2", 1_000_000),
            _frame("f3", 1_400_000),
        ]
        samples, _ = _time_bucket_grouper(window_ms=1000.0).group(frames, scene_id="sc")
        assert len(samples) == 2
        assert {sf.frame_id for sf in samples[0].sensor_frames} == {"f0", "f1"}
        assert {sf.frame_id for sf in samples[1].sensor_frames} == {"f2", "f3"}

    def test_group_by_time_bucket_ordering(self) -> None:
        frames = [_frame(f"f{i:03d}", i * 600_000) for i in range(6)]
        samples, _ = _time_bucket_grouper(window_ms=500.0).group(
            frames, scene_id="sc-001"
        )
        for i, s in enumerate(samples):
            assert s.sample_id == f"sc-001-sample-{i:06d}"

    def test_fixed_bucket_not_rolling_window(self) -> None:
        # If this were a rolling window, f0+f1+f2 would all merge (each 900ms apart).
        # Fixed buckets: bucket 0 = [f0, f1], bucket 1 = [f2, f3].
        frames = [
            _frame("f0", 0),
            _frame("f1", 900_000),
            _frame("f2", 1_000_000),
            _frame("f3", 1_900_000),
        ]
        samples, _ = _time_bucket_grouper(window_ms=1000.0).group(frames, scene_id="sc")
        assert len(samples) == 2
        assert {sf.frame_id for sf in samples[0].sensor_frames} == {"f0", "f1"}
        assert {sf.frame_id for sf in samples[1].sensor_frames} == {"f2", "f3"}

    def test_close_frames_land_in_single_bucket(self) -> None:
        frames = [_frame(f"f{i}", i * 10_000) for i in range(5)]
        samples, _ = _time_bucket_grouper(window_ms=500.0).group(frames, scene_id="sc")
        assert len(samples) == 1

    def test_produces_multiple_samples_from_spread_frames(self) -> None:
        frames = [_frame(f"f{i}", i * 600_000) for i in range(8)]
        samples, _ = _time_bucket_grouper(window_ms=500.0).group(frames, scene_id="sc")
        assert len(samples) > 1

    def test_each_sample_has_sensor_frames(self) -> None:
        frames = [_frame(f"f{i}", i * 600_000) for i in range(4)]
        samples, _ = _time_bucket_grouper(window_ms=500.0).group(frames, scene_id="sc")
        for s in samples:
            assert len(s.sensor_frames) >= 1

    def test_sample_ids_do_not_copy_source_sample_id(self) -> None:
        source_id = "ns-sample-aabb"
        frames = [
            _frame(f"f{i}", i * 600_000, source_sample_id=source_id) for i in range(6)
        ]
        samples, _ = _time_bucket_grouper(window_ms=500.0).group(
            frames, scene_id="sc-001"
        )
        for s in samples:
            assert source_id not in s.sample_id

    def test_none_window_raises(self) -> None:
        grouper = SampleGrouper(
            SampleGroupingConfig(
                strategy=SampleGroupingStrategy.TIME_BUCKET,
                sample_time_window_ms=None,
            )
        )
        with pytest.raises(ValueError, match="sample_time_window_ms"):
            grouper.group([_frame("f0", 0)], scene_id="sc")

    def test_zero_window_raises(self) -> None:
        grouper = SampleGrouper(
            SampleGroupingConfig(
                strategy=SampleGroupingStrategy.TIME_BUCKET,
                sample_time_window_ms=0,
            )
        )
        with pytest.raises(ValueError, match="sample_time_window_ms"):
            grouper.group([_frame("f0", 0)], scene_id="sc")


# ── NEAREST_TIMESTAMP ─────────────────────────────────────────────────────────


def _nearest_ts_grouper(
    reference_channel: str = "LIDAR_TOP",
    sync_policy: SensorSyncPolicy = SensorSyncPolicy.BEST_EFFORT,
    sync_tolerance_ms: float = 50.0,
) -> SampleGrouper:
    return SampleGrouper(
        SampleGroupingConfig(
            strategy=SampleGroupingStrategy.NEAREST_TIMESTAMP,
            reference_channel=reference_channel,
            sync_policy=sync_policy,
            sync_tolerance_ms=sync_tolerance_ms,
        )
    )


class TestNearestTimestampGrouper:
    def test_basic_two_channel_grouping(self) -> None:
        frames = [
            _frame("lidar0", 0, "LIDAR_TOP"),
            _frame("cam0", 10_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        assert len(samples) == 1
        channels = {sf.channel for sf in samples[0].sensor_frames}
        assert channels == {"LIDAR_TOP", "CAM_FRONT"}

    def test_reference_frames_drive_sample_count(self) -> None:
        # 3 lidar frames, 5 camera frames — should produce exactly 3 samples
        frames = [_frame(f"lidar{i}", i * 100_000, "LIDAR_TOP") for i in range(3)] + [
            _frame(f"cam{i}", i * 60_000, "CAM_FRONT") for i in range(5)
        ]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        assert len(samples) == 3

    def test_sample_id_format(self) -> None:
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("l1", 100_000, "LIDAR_TOP"),
        ]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        assert samples[0].sample_id == "sc-sample-000000"
        assert samples[1].sample_id == "sc-sample-000001"

    def test_sample_timestamp_is_reference_timestamp(self) -> None:
        frames = [
            _frame("l0", 50_000, "LIDAR_TOP"),
            _frame("c0", 75_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        assert samples[0].timestamp_us == 50_000

    def test_frame_index_increments_sequentially(self) -> None:
        frames = [_frame(f"l{i}", i * 100_000, "LIDAR_TOP") for i in range(4)]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        for idx, s in enumerate(samples):
            assert s.frame_index == idx

    def test_nearest_frame_selected_from_other_channel(self) -> None:
        # Reference at t=500ms; cam at t=100ms and t=480ms — pick t=480ms (closer)
        frames = [
            _frame("l0", 500_000, "LIDAR_TOP"),
            _frame("c0", 100_000, "CAM_FRONT"),
            _frame("c1", 480_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        cam_frames = [
            sf for sf in samples[0].sensor_frames if sf.channel == "CAM_FRONT"
        ]
        assert len(cam_frames) == 1
        assert cam_frames[0].frame_id == "c1"

    def test_empty_frames_returns_empty(self) -> None:
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, report = grouper.group([], scene_id="sc")
        assert samples == []
        assert report.total_samples_built == 0

    def test_no_reference_channel_raises(self) -> None:
        grouper = SampleGrouper(
            SampleGroupingConfig(
                strategy=SampleGroupingStrategy.NEAREST_TIMESTAMP,
                reference_channel=None,
            )
        )
        with pytest.raises(ValueError, match="reference_channel"):
            grouper.group([_frame("f0", 0)], scene_id="sc")

    def test_missing_reference_frames_returns_empty(self) -> None:
        # Only CAM_FRONT frames, but reference is LIDAR_TOP
        frames = [_frame("c0", 0, "CAM_FRONT")]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, report = grouper.group(frames, scene_id="sc")
        assert samples == []
        assert report.total_samples_built == 0

    def test_only_reference_channel_frames(self) -> None:
        # No other channels — one sample per reference frame, each with one sensor_frame
        frames = [_frame(f"l{i}", i * 100_000, "LIDAR_TOP") for i in range(3)]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        assert len(samples) == 3
        for s in samples:
            assert len(s.sensor_frames) == 1
            assert s.sensor_frames[0].channel == "LIDAR_TOP"

    def test_sensor_frame_metadata_preserved(self) -> None:
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("c0", 5_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, _ = grouper.group(frames, scene_id="sc")
        cam_sf = next(
            sf for sf in samples[0].sensor_frames if sf.channel == "CAM_FRONT"
        )
        assert cam_sf.frame_id == "c0"
        assert cam_sf.timestamp_us == 5_000
        assert cam_sf.uri == "s3://data/c0.jpg"


class TestNearestTimestampSyncPolicy:
    def test_best_effort_includes_all_nearest_regardless_of_distance(self) -> None:
        # Camera is 1 second away — BEST_EFFORT still includes it
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("c0", 1_000_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(
            reference_channel="LIDAR_TOP",
            sync_policy=SensorSyncPolicy.BEST_EFFORT,
            sync_tolerance_ms=50.0,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        channels = {sf.channel for sf in samples[0].sensor_frames}
        assert "CAM_FRONT" in channels

    def test_within_tolerance_excludes_frames_outside_window(self) -> None:
        # Camera is 100ms away, tolerance is 50ms — excluded
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("c0", 100_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(
            reference_channel="LIDAR_TOP",
            sync_policy=SensorSyncPolicy.WITHIN_TOLERANCE,
            sync_tolerance_ms=50.0,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        channels = {sf.channel for sf in samples[0].sensor_frames}
        assert "CAM_FRONT" not in channels
        assert "LIDAR_TOP" in channels

    def test_within_tolerance_includes_frames_inside_window(self) -> None:
        # Camera is 30ms away, tolerance is 50ms — included
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("c0", 30_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(
            reference_channel="LIDAR_TOP",
            sync_policy=SensorSyncPolicy.WITHIN_TOLERANCE,
            sync_tolerance_ms=50.0,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        channels = {sf.channel for sf in samples[0].sensor_frames}
        assert "CAM_FRONT" in channels

    def test_within_tolerance_exact_boundary_is_included(self) -> None:
        # Exactly at tolerance boundary (<=) — included
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("c0", 50_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(
            reference_channel="LIDAR_TOP",
            sync_policy=SensorSyncPolicy.WITHIN_TOLERANCE,
            sync_tolerance_ms=50.0,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        channels = {sf.channel for sf in samples[0].sensor_frames}
        assert "CAM_FRONT" in channels

    def test_exact_policy_includes_same_timestamp(self) -> None:
        frames = [
            _frame("l0", 1_000_000, "LIDAR_TOP"),
            _frame("c0", 1_000_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(
            reference_channel="LIDAR_TOP",
            sync_policy=SensorSyncPolicy.EXACT,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        channels = {sf.channel for sf in samples[0].sensor_frames}
        assert "CAM_FRONT" in channels

    def test_exact_policy_excludes_non_exact_timestamp(self) -> None:
        frames = [
            _frame("l0", 1_000_000, "LIDAR_TOP"),
            _frame("c0", 1_000_001, "CAM_FRONT"),  # 1µs off
        ]
        grouper = _nearest_ts_grouper(
            reference_channel="LIDAR_TOP",
            sync_policy=SensorSyncPolicy.EXACT,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        channels = {sf.channel for sf in samples[0].sensor_frames}
        assert "CAM_FRONT" not in channels

    def test_sample_created_even_when_all_non_ref_channels_excluded(self) -> None:
        # All non-reference frames out of tolerance — sample still created with only ref
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("c0", 500_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(
            reference_channel="LIDAR_TOP",
            sync_policy=SensorSyncPolicy.WITHIN_TOLERANCE,
            sync_tolerance_ms=50.0,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        assert len(samples) == 1
        assert len(samples[0].sensor_frames) == 1
        assert samples[0].sensor_frames[0].channel == "LIDAR_TOP"


class TestNearestTimestampReport:
    def test_report_counts_match_reference_frame_count(self) -> None:
        frames = [_frame(f"l{i}", i * 100_000, "LIDAR_TOP") for i in range(4)]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        samples, report = grouper.group(frames, scene_id="sc")
        assert report.total_samples_built == 4
        assert report.sample_count_before_filtering == 4
        assert report.sample_count_after_filtering == 4

    def test_empty_report_when_no_reference_frames(self) -> None:
        frames = [_frame("c0", 0, "CAM_FRONT")]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        _, report = grouper.group(frames, scene_id="sc")
        assert report.total_samples_built == 0
        assert report.sample_count_before_filtering == 0
        assert report.sample_count_after_filtering == 0

    def test_filter_counts_zero_without_required_channels(self) -> None:
        frames = [
            _frame("l0", 0, "LIDAR_TOP"),
            _frame("c0", 10_000, "CAM_FRONT"),
        ]
        grouper = _nearest_ts_grouper(reference_channel="LIDAR_TOP")
        _, report = grouper.group(frames, scene_id="sc")
        assert report.dropped_sample_count == 0
        assert report.warned_sample_count == 0
        assert report.samples_with_missing_channels_count == 0


# ── SampleGroupingReport ──────────────────────────────────────────────────────


class TestSampleGroupingReport:
    def test_report_counts_match_current_samples_frame_id(self) -> None:
        frames = [
            _frame(f"f{i}", i * 10_000, source_frame_id=f"g{i}") for i in range(5)
        ]
        samples, report = _frame_id_grouper().group(frames, scene_id="sc")
        assert report.total_samples_built == len(samples)
        assert report.sample_count_before_filtering == len(samples)
        assert report.sample_count_after_filtering == len(samples)

    def test_report_counts_match_current_samples_time_bucket(self) -> None:
        frames = [_frame(f"f{i}", i * 600_000) for i in range(6)]
        samples, report = _time_bucket_grouper(window_ms=500.0).group(
            frames, scene_id="sc"
        )
        assert report.total_samples_built == len(samples)
        assert report.sample_count_before_filtering == len(samples)
        assert report.sample_count_after_filtering == len(samples)

    def test_filter_counts_are_zero_in_phase1(self) -> None:
        frames = [
            _frame(f"f{i}", i * 10_000, source_frame_id=f"g{i}") for i in range(3)
        ]
        _, report = _frame_id_grouper().group(frames, scene_id="sc")
        assert report.dropped_sample_count == 0
        assert report.warned_sample_count == 0
        assert report.samples_with_missing_channels_count == 0
        assert report.missing_channel_counts_by_channel == {}

    def test_empty_frames_returns_empty_samples_and_zero_report(self) -> None:
        samples, report = _frame_id_grouper().group([], scene_id="sc")
        assert samples == []
        assert report.total_samples_built == 0
        assert report.sample_count_before_filtering == 0
        assert report.sample_count_after_filtering == 0
        assert report.dropped_sample_count == 0
        assert report.warned_sample_count == 0

    def test_empty_frames_time_bucket_returns_empty_and_zero_report(self) -> None:
        samples, report = _time_bucket_grouper().group([], scene_id="sc")
        assert samples == []
        assert report.total_samples_built == 0
        assert report.sample_count_before_filtering == 0
        assert report.sample_count_after_filtering == 0

    def test_report_is_independent_per_group_call(self) -> None:
        grouper = _frame_id_grouper()
        frames_a = [
            _frame(f"a{i}", i * 10_000, source_frame_id=f"ga{i}") for i in range(3)
        ]
        frames_b = [
            _frame(f"b{i}", i * 10_000, source_frame_id=f"gb{i}") for i in range(5)
        ]
        _, report_a = grouper.group(frames_a, scene_id="sc-a")
        _, report_b = grouper.group(frames_b, scene_id="sc-b")
        assert report_a.total_samples_built == 3
        assert report_b.total_samples_built == 5


# ── SampleGroupingReport.merge() ─────────────────────────────────────────────


class TestSampleGroupingReportMerge:
    def test_merge_adds_numeric_counts(self) -> None:
        a = SampleGroupingReport(
            total_samples_built=3,
            sample_count_before_filtering=3,
            sample_count_after_filtering=3,
            dropped_sample_count=0,
            warned_sample_count=0,
            samples_with_missing_channels_count=0,
        )
        b = SampleGroupingReport(
            total_samples_built=5,
            sample_count_before_filtering=5,
            sample_count_after_filtering=4,
            dropped_sample_count=1,
            warned_sample_count=2,
            samples_with_missing_channels_count=1,
        )
        a.merge(b)
        assert a.total_samples_built == 8
        assert a.sample_count_before_filtering == 8
        assert a.sample_count_after_filtering == 7
        assert a.dropped_sample_count == 1
        assert a.warned_sample_count == 2
        assert a.samples_with_missing_channels_count == 1

    def test_merge_combines_missing_channel_counts_by_channel(self) -> None:
        a = SampleGroupingReport(
            missing_channel_counts_by_channel={"CAM_FRONT": 2, "LIDAR_TOP": 1}
        )
        b = SampleGroupingReport(
            missing_channel_counts_by_channel={"CAM_FRONT": 3, "CAM_BACK": 1}
        )
        a.merge(b)
        assert a.missing_channel_counts_by_channel["CAM_FRONT"] == 5
        assert a.missing_channel_counts_by_channel["LIDAR_TOP"] == 1
        assert a.missing_channel_counts_by_channel["CAM_BACK"] == 1

    def test_merge_with_empty_other_is_identity(self) -> None:
        a = SampleGroupingReport(
            total_samples_built=4,
            sample_count_before_filtering=4,
            sample_count_after_filtering=4,
            missing_channel_counts_by_channel={"CAM_FRONT": 2},
        )
        empty = SampleGroupingReport()
        a.merge(empty)
        assert a.total_samples_built == 4
        assert a.missing_channel_counts_by_channel == {"CAM_FRONT": 2}

    def test_merge_into_empty_base_copies_values(self) -> None:
        base = SampleGroupingReport()
        other = SampleGroupingReport(
            total_samples_built=7,
            sample_count_before_filtering=7,
            sample_count_after_filtering=6,
            dropped_sample_count=1,
            missing_channel_counts_by_channel={"CAM_BACK": 3},
        )
        base.merge(other)
        assert base.total_samples_built == 7
        assert base.dropped_sample_count == 1
        assert base.missing_channel_counts_by_channel == {"CAM_BACK": 3}

    def test_merge_does_not_mutate_other(self) -> None:
        a = SampleGroupingReport(total_samples_built=2)
        b = SampleGroupingReport(
            total_samples_built=5,
            missing_channel_counts_by_channel={"CAM_FRONT": 1},
        )
        a.merge(b)
        assert b.total_samples_built == 5
        assert b.missing_channel_counts_by_channel == {"CAM_FRONT": 1}


# ── Phase 3: required_channels + missing_channel_policy ───────────────────────


def _grouper_with_policy(
    *,
    strategy: SampleGroupingStrategy = SampleGroupingStrategy.FRAME_ID,
    required_channels: list[str],
    policy: MissingChannelPolicy,
    window_ms: float = 500.0,
) -> SampleGrouper:
    return SampleGrouper(
        SampleGroupingConfig(
            strategy=strategy,
            sample_time_window_ms=window_ms,
            required_channels=required_channels,
            missing_channel_policy=policy,
        )
    )


def _multi_channel_frame(
    frame_id: str,
    timestamp_us: int,
    channel: str,
    *,
    source_frame_id: str | None = None,
) -> RawSensorFrameManifest:
    return RawSensorFrameManifest(
        frame_id=frame_id,
        timestamp_us=timestamp_us,
        channel=channel,
        modality=SensorModality.CAMERA,
        uri=f"s3://data/{frame_id}.jpg",
        source_frame_id=source_frame_id,
    )


class TestRequiredChannelsEmpty:
    """When required_channels is empty the policy is never applied."""

    def test_frame_id_unchanged_when_no_required_channels(self) -> None:
        frames = [
            _frame(f"f{i}", i * 10_000, source_frame_id=f"g{i}") for i in range(4)
        ]
        grouper = SampleGrouper(
            SampleGroupingConfig(
                strategy=SampleGroupingStrategy.FRAME_ID,
                required_channels=[],
                missing_channel_policy=MissingChannelPolicy.DROP_SAMPLE,
            )
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert len(samples) == 4
        assert report.dropped_sample_count == 0
        assert report.warned_sample_count == 0
        assert report.samples_with_missing_channels_count == 0
        assert report.missing_channel_counts_by_channel == {}
        assert (
            report.sample_count_before_filtering == report.sample_count_after_filtering
        )

    def test_time_bucket_unchanged_when_no_required_channels(self) -> None:
        frames = [_frame(f"f{i}", i * 600_000) for i in range(6)]
        grouper = SampleGrouper(
            SampleGroupingConfig(
                strategy=SampleGroupingStrategy.TIME_BUCKET,
                sample_time_window_ms=500.0,
                required_channels=[],
                missing_channel_policy=MissingChannelPolicy.FAIL_SCENE,
            )
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert len(samples) > 0
        assert report.dropped_sample_count == 0
        assert report.warned_sample_count == 0
        assert (
            report.sample_count_before_filtering == report.sample_count_after_filtering
        )


class TestAllChannelsPresent:
    """No policy action when all required channels are present."""

    def test_no_counts_incremented_when_all_present(self) -> None:
        # Each sample group has both CAM_FRONT and LIDAR_TOP
        frames = [
            _multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0"),
            _multi_channel_frame("fl", 1, "LIDAR_TOP", source_frame_id="g0"),
        ]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert len(samples) == 1
        assert report.warned_sample_count == 0
        assert report.dropped_sample_count == 0
        assert report.samples_with_missing_channels_count == 0
        assert report.missing_channel_counts_by_channel == {}
        assert (
            report.sample_count_before_filtering
            == report.sample_count_after_filtering
            == 1
        )


class TestKeepWithWarning:
    """KEEP_WITH_WARNING: sample is kept, counts increment."""

    def test_incomplete_sample_kept(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert len(samples) == 1

    def test_warned_sample_count_increments(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        _, report = grouper.group(frames, scene_id="sc")
        assert report.warned_sample_count == 1

    def test_samples_with_missing_channels_count_increments(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        _, report = grouper.group(frames, scene_id="sc")
        assert report.samples_with_missing_channels_count == 1

    def test_missing_channel_counts_by_channel_increments(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        _, report = grouper.group(frames, scene_id="sc")
        assert report.missing_channel_counts_by_channel == {"LIDAR_TOP": 1}

    def test_before_equals_after_because_sample_kept(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert report.sample_count_before_filtering == 1
        assert report.sample_count_after_filtering == 1
        assert len(samples) == 1

    def test_complete_and_incomplete_samples_only_incomplete_warned(self) -> None:
        frames = [
            # sample 0: complete
            _multi_channel_frame("fc0", 0, "CAM_FRONT", source_frame_id="g0"),
            _multi_channel_frame("fl0", 1, "LIDAR_TOP", source_frame_id="g0"),
            # sample 1: missing LIDAR_TOP
            _multi_channel_frame("fc1", 1_000_000, "CAM_FRONT", source_frame_id="g1"),
        ]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert len(samples) == 2
        assert report.warned_sample_count == 1
        assert report.samples_with_missing_channels_count == 1
        assert report.sample_count_before_filtering == 2
        assert report.sample_count_after_filtering == 2

    def test_multiple_missing_channels_counted_separately(self) -> None:
        # sample has only CAM_BACK; missing CAM_FRONT and LIDAR_TOP
        frames = [_multi_channel_frame("fb", 0, "CAM_BACK", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
        )
        _, report = grouper.group(frames, scene_id="sc")
        assert report.missing_channel_counts_by_channel["CAM_FRONT"] == 1
        assert report.missing_channel_counts_by_channel["LIDAR_TOP"] == 1
        assert report.samples_with_missing_channels_count == 1

    def test_applies_after_time_bucket_grouping(self) -> None:
        # Two buckets: bucket-0 has both channels, bucket-1 has only CAM_FRONT
        frames = [
            _multi_channel_frame("fc0", 0, "CAM_FRONT"),
            _multi_channel_frame("fl0", 1, "LIDAR_TOP"),
            _multi_channel_frame("fc1", 1_000_000, "CAM_FRONT"),
        ]
        grouper = _grouper_with_policy(
            strategy=SampleGroupingStrategy.TIME_BUCKET,
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.KEEP_WITH_WARNING,
            window_ms=500.0,
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert len(samples) == 2
        assert report.warned_sample_count == 1
        assert report.sample_count_before_filtering == 2
        assert report.sample_count_after_filtering == 2


class TestDropSample:
    """DROP_SAMPLE: incomplete sample is removed, order preserved, IDs not renumbered."""

    def test_incomplete_sample_removed(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.DROP_SAMPLE,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        assert len(samples) == 0

    def test_dropped_sample_count_increments(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.DROP_SAMPLE,
        )
        _, report = grouper.group(frames, scene_id="sc")
        assert report.dropped_sample_count == 1

    def test_sample_count_after_filtering_reduced(self) -> None:
        frames = [
            _multi_channel_frame("fc0", 0, "CAM_FRONT", source_frame_id="g0"),
            _multi_channel_frame("fl0", 1, "LIDAR_TOP", source_frame_id="g0"),
            _multi_channel_frame("fc1", 1_000_000, "CAM_FRONT", source_frame_id="g1"),
        ]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.DROP_SAMPLE,
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert report.sample_count_before_filtering == 2
        assert report.sample_count_after_filtering == 1
        assert len(samples) == 1

    def test_retained_sample_order_preserved(self) -> None:
        # samples 0 and 2 are complete; sample 1 is dropped
        frames = [
            # g0: complete, timestamp=0
            _multi_channel_frame("fc0", 0, "CAM_FRONT", source_frame_id="g0"),
            _multi_channel_frame("fl0", 1, "LIDAR_TOP", source_frame_id="g0"),
            # g1: incomplete (only CAM_FRONT), timestamp=1_000_000
            _multi_channel_frame("fc1", 1_000_000, "CAM_FRONT", source_frame_id="g1"),
            # g2: complete, timestamp=2_000_000
            _multi_channel_frame("fc2", 2_000_000, "CAM_FRONT", source_frame_id="g2"),
            _multi_channel_frame("fl2", 2_000_001, "LIDAR_TOP", source_frame_id="g2"),
        ]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.DROP_SAMPLE,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        assert len(samples) == 2
        assert samples[0].timestamp_us == 0
        assert samples[1].timestamp_us == 2_000_000

    def test_sample_ids_not_renumbered_after_drop(self) -> None:
        # sample at idx=0 is complete → sc-sample-000000
        # sample at idx=1 is dropped
        # sample at idx=2 is complete → sc-sample-000002 (gap preserved)
        frames = [
            # g0: complete
            _multi_channel_frame("fc0", 0, "CAM_FRONT", source_frame_id="g0"),
            _multi_channel_frame("fl0", 1, "LIDAR_TOP", source_frame_id="g0"),
            # g1: incomplete
            _multi_channel_frame("fc1", 1_000_000, "CAM_FRONT", source_frame_id="g1"),
            # g2: complete
            _multi_channel_frame("fc2", 2_000_000, "CAM_FRONT", source_frame_id="g2"),
            _multi_channel_frame("fl2", 2_000_001, "LIDAR_TOP", source_frame_id="g2"),
        ]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.DROP_SAMPLE,
        )
        samples, _ = grouper.group(frames, scene_id="sc")
        assert len(samples) == 2
        assert samples[0].sample_id == "sc-sample-000000"
        assert samples[1].sample_id == "sc-sample-000002"

    def test_missing_channel_counts_still_tracked_for_dropped_samples(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.DROP_SAMPLE,
        )
        _, report = grouper.group(frames, scene_id="sc")
        assert report.missing_channel_counts_by_channel == {"LIDAR_TOP": 1}
        assert report.samples_with_missing_channels_count == 1


class TestFailScene:
    """FAIL_SCENE: raises ValueError with scene/sample/channel info."""

    def test_raises_on_missing_required_channel(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.FAIL_SCENE,
        )
        with pytest.raises(ValueError):
            grouper.group(frames, scene_id="sc-007")

    def test_error_contains_scene_id(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["LIDAR_TOP"],
            policy=MissingChannelPolicy.FAIL_SCENE,
        )
        with pytest.raises(ValueError, match="sc-007"):
            grouper.group(frames, scene_id="sc-007")

    def test_error_contains_sample_id(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["LIDAR_TOP"],
            policy=MissingChannelPolicy.FAIL_SCENE,
        )
        with pytest.raises(ValueError, match="sample"):
            grouper.group(frames, scene_id="sc")

    def test_error_contains_missing_channel_name(self) -> None:
        frames = [_multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0")]
        grouper = _grouper_with_policy(
            required_channels=["LIDAR_TOP"],
            policy=MissingChannelPolicy.FAIL_SCENE,
        )
        with pytest.raises(ValueError, match="LIDAR_TOP"):
            grouper.group(frames, scene_id="sc")

    def test_no_raise_when_all_channels_present(self) -> None:
        frames = [
            _multi_channel_frame("fc", 0, "CAM_FRONT", source_frame_id="g0"),
            _multi_channel_frame("fl", 1, "LIDAR_TOP", source_frame_id="g0"),
        ]
        grouper = _grouper_with_policy(
            required_channels=["CAM_FRONT", "LIDAR_TOP"],
            policy=MissingChannelPolicy.FAIL_SCENE,
        )
        samples, report = grouper.group(frames, scene_id="sc")
        assert len(samples) == 1
        assert report.dropped_sample_count == 0
