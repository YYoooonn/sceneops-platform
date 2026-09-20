"""Tests for fixed-frequency timeline generation (SceneOps V2 Request 2.2
§7-9, §42). Pure integer arithmetic -- no I/O, no manifest involved."""

from __future__ import annotations

import pytest

from sceneops_core.episodes.alignment import (
    InvalidAlignmentConfigError,
    InvalidEpisodeBoundsError,
    generate_fixed_frequency_timeline,
    quantize_period_us,
)


class TestQuantizePeriodUs:
    def test_1hz_is_exactly_one_second(self) -> None:
        assert quantize_period_us(1.0) == 1_000_000

    def test_128hz_rounds_half_up_not_banker(self) -> None:
        # 1_000_000 / 128 = 7812.5 exactly -- ROUND_HALF_UP must give 7813,
        # not Python's default banker's rounding (which would give 7812).
        assert quantize_period_us(128.0) == 7813

    def test_extremely_high_frequency_is_rejected(self) -> None:
        with pytest.raises(InvalidAlignmentConfigError):
            quantize_period_us(3_000_000.0)  # period=0.33us, rounds to dt_us=0

    def test_exact_half_microsecond_period_rounds_up_not_to_zero(self) -> None:
        # 2_000_000 Hz -> period exactly 0.5us -- ROUND_HALF_UP must give 1,
        # not 0 (and not banker's-rounding's 0).
        assert quantize_period_us(2_000_000.0) == 1


class TestFixedFrequencyTimeline:
    def test_duration_exactly_divisible_by_dt(self) -> None:
        timeline = generate_fixed_frequency_timeline(
            start_timestamp_us=0, end_timestamp_us=10_000_000, target_frequency_hz=1.0
        )
        assert timeline.dt_us == 1_000_000
        assert timeline.step_count == 11
        assert timeline.timestamps_us[0] == 0
        assert timeline.timestamps_us[-1] == 10_000_000  # end lands exactly on-grid
        assert timeline.timestamps_us == tuple(range(0, 10_000_001, 1_000_000))

    def test_duration_not_divisible_by_dt_excludes_end(self) -> None:
        timeline = generate_fixed_frequency_timeline(
            start_timestamp_us=0, end_timestamp_us=10_500_000, target_frequency_hz=1.0
        )
        assert timeline.step_count == 11
        assert timeline.timestamps_us[-1] == 10_000_000  # not 10_500_000
        assert all(t <= 10_500_000 for t in timeline.timestamps_us)

    def test_zero_duration_episode_yields_single_step_at_start(self) -> None:
        timeline = generate_fixed_frequency_timeline(
            start_timestamp_us=5_000, end_timestamp_us=5_000, target_frequency_hz=1.0
        )
        assert timeline.step_count == 1
        assert timeline.timestamps_us == (5_000,)

    def test_episode_shorter_than_one_dt_yields_single_step_at_start(self) -> None:
        timeline = generate_fixed_frequency_timeline(
            start_timestamp_us=0, end_timestamp_us=500_000, target_frequency_hz=1.0
        )
        assert timeline.step_count == 1
        assert timeline.timestamps_us == (0,)

    def test_achieved_frequency_reflects_quantization_not_the_request(self) -> None:
        timeline = generate_fixed_frequency_timeline(
            start_timestamp_us=0, end_timestamp_us=1_000_000, target_frequency_hz=128.0
        )
        assert timeline.dt_us == 7813
        assert timeline.achieved_frequency_hz == pytest.approx(1_000_000 / 7813)
        assert timeline.achieved_frequency_hz != 128.0

    def test_start_missing_fails_explicitly(self) -> None:
        with pytest.raises(InvalidEpisodeBoundsError):
            generate_fixed_frequency_timeline(
                start_timestamp_us=None,
                end_timestamp_us=1_000_000,
                target_frequency_hz=1.0,
            )

    def test_end_missing_fails_explicitly(self) -> None:
        with pytest.raises(InvalidEpisodeBoundsError):
            generate_fixed_frequency_timeline(
                start_timestamp_us=0, end_timestamp_us=None, target_frequency_hz=1.0
            )

    def test_start_after_end_fails_explicitly(self) -> None:
        with pytest.raises(InvalidEpisodeBoundsError):
            generate_fixed_frequency_timeline(
                start_timestamp_us=1_000_000,
                end_timestamp_us=0,
                target_frequency_hz=1.0,
            )

    def test_no_timestamp_ever_exceeds_end(self) -> None:
        timeline = generate_fixed_frequency_timeline(
            start_timestamp_us=0, end_timestamp_us=999_999, target_frequency_hz=1.0
        )
        assert all(t <= 999_999 for t in timeline.timestamps_us)

    def test_spacing_is_always_exactly_dt_us(self) -> None:
        timeline = generate_fixed_frequency_timeline(
            start_timestamp_us=1_000, end_timestamp_us=51_000, target_frequency_hz=10.0
        )
        deltas = [
            b - a for a, b in zip(timeline.timestamps_us, timeline.timestamps_us[1:])
        ]
        assert all(d == timeline.dt_us for d in deltas)
