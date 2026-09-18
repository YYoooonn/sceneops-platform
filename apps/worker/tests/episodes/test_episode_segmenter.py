"""Tests for EpisodeSegmenter (SceneOps V2 Request 13).

Covers the three supported strategies — MISSION_BOUNDARY (incl. the explicit
whole-run fallback), WHOLE_RUN, and FIXED_WINDOW — in isolation from
EpisodeBuilder's materialization logic (see test_episode_builder.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sceneops_core.episodes.schemas import EpisodeSegmentationConfig, EpisodeSource
from sceneops_core.observations.schemas import RawSensorFrameManifest
from sceneops_core.robots.schemas import MissionRecord, MissionStatus, RobotStateRecord
from sceneops_worker.episodes.building import EpisodeSegmenter


def _frame(timestamp_us: int) -> RawSensorFrameManifest:
    return RawSensorFrameManifest(
        frame_id=f"f-{timestamp_us}",
        timestamp_us=timestamp_us,
        channel="CAM_FRONT",
        modality="camera",
        uri="",
    )


def _state(timestamp_us: int) -> RobotStateRecord:
    return RobotStateRecord(
        state_id=f"s-{timestamp_us}",
        robot_id="r1",
        timestamp_us=timestamp_us,
        battery=90.0,
    )


def _mission(
    mission_id: str, *, start_s: float | None, end_s: float | None = None
) -> MissionRecord:
    return MissionRecord(
        mission_id=mission_id,
        robot_id="r1",
        status=MissionStatus.COMPLETED,
        started_at=datetime.fromtimestamp(start_s, tz=timezone.utc)
        if start_s is not None
        else None,
        ended_at=datetime.fromtimestamp(end_s, tz=timezone.utc)
        if end_s is not None
        else None,
    )


class TestMissionBoundary:
    def test_one_dated_mission_returns_one_window(self) -> None:
        source = EpisodeSource(missions=[_mission("m1", start_s=1.0, end_s=3.0)])
        config = EpisodeSegmentationConfig()  # default: mission_boundary

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert len(windows) == 1
        assert windows[0].start_us == 1_000_000
        assert windows[0].end_us == 3_000_000
        assert windows[0].mission_id == "m1"
        assert windows[0].segment_index == 0

    def test_multiple_missions_return_ordered_windows(self) -> None:
        # Deliberately out of chronological order in the source list.
        source = EpisodeSource(
            missions=[
                _mission("m-late", start_s=10.0, end_s=12.0),
                _mission("m-early", start_s=1.0, end_s=3.0),
            ]
        )
        config = EpisodeSegmentationConfig()

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert [w.mission_id for w in windows] == ["m-early", "m-late"]
        assert [w.segment_index for w in windows] == [0, 1]
        assert windows[0].start_us < windows[1].start_us

    def test_no_dated_missions_falls_back_to_whole_run(self) -> None:
        source = EpisodeSource(missions=[_mission("m1", start_s=None)])
        config = EpisodeSegmentationConfig()

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert windows == EpisodeSegmenter().segment(
            source=EpisodeSource(),
            config=EpisodeSegmentationConfig(strategy="whole_run"),
        )

    def test_undated_missions_are_ignored_dated_ones_still_used(self) -> None:
        source = EpisodeSource(
            missions=[
                _mission("m-undated", start_s=None),
                _mission("m-dated", start_s=1.0, end_s=2.0),
            ]
        )
        config = EpisodeSegmentationConfig()

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert len(windows) == 1
        assert windows[0].mission_id == "m-dated"

    def test_open_ended_mission_has_none_end_us(self) -> None:
        source = EpisodeSource(missions=[_mission("m1", start_s=1.0, end_s=None)])
        config = EpisodeSegmentationConfig()

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert windows[0].end_us is None


class TestWholeRun:
    def test_ignores_missions_returns_exactly_one_window(self) -> None:
        source = EpisodeSource(
            missions=[
                _mission("m1", start_s=1.0, end_s=2.0),
                _mission("m2", start_s=5.0, end_s=6.0),
            ],
            frames=[_frame(1_000_000)],
        )
        config = EpisodeSegmentationConfig(strategy="whole_run")

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert len(windows) == 1
        assert windows[0].mission is None
        assert windows[0].segment_index == 0

    def test_boundary_covers_full_source_regardless_of_frame_presence(self) -> None:
        config = EpisodeSegmentationConfig(strategy="whole_run")

        windows = EpisodeSegmenter().segment(source=EpisodeSource(), config=config)

        assert len(windows) == 1
        assert windows[0].start_us == 0
        assert windows[0].end_us is None


class TestFixedWindow:
    def test_run_within_exact_multiple_yields_no_trailing_window(self) -> None:
        # Run data spans [0, 89s] — strictly less than 3*30s=90s, so it's
        # fully covered by exactly 3 windows with no need for a 4th.
        source = EpisodeSource(
            frames=[_frame(0), _frame(89_000_000)],
        )
        config = EpisodeSegmentationConfig(
            strategy="fixed_window", fixed_window_duration_ms=30_000
        )

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert [(w.start_us, w.end_us) for w in windows] == [
            (0, 30_000_000),
            (30_000_000, 60_000_000),
            (60_000_000, 90_000_000),
        ]

    def test_final_partial_window_is_retained(self) -> None:
        # run: 0s..95s, window: 30s -> [0,30),[30,60),[60,90),[90,120) — the
        # last window is nominally [90,120) but the source only has data up
        # to 95s, so it's a partial window in practice; it must still appear.
        source = EpisodeSource(frames=[_frame(0), _frame(95_000_000)])
        config = EpisodeSegmentationConfig(
            strategy="fixed_window", fixed_window_duration_ms=30_000
        )

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert len(windows) == 4
        assert windows[-1].start_us == 90_000_000
        assert windows[-1].end_us == 120_000_000

    def test_very_short_run_yields_one_window(self) -> None:
        source = EpisodeSource(frames=[_frame(0), _frame(5_000_000)])
        config = EpisodeSegmentationConfig(
            strategy="fixed_window", fixed_window_duration_ms=30_000
        )

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert len(windows) == 1
        assert windows[0].start_us == 0
        assert windows[0].end_us == 30_000_000

    def test_empty_source_yields_no_windows(self) -> None:
        config = EpisodeSegmentationConfig(
            strategy="fixed_window", fixed_window_duration_ms=30_000
        )

        windows = EpisodeSegmenter().segment(source=EpisodeSource(), config=config)

        assert windows == []

    def test_robot_states_alone_are_a_usable_timestamp_source(self) -> None:
        source = EpisodeSource(robot_states=[_state(0), _state(45_000_000)])
        config = EpisodeSegmentationConfig(
            strategy="fixed_window", fixed_window_duration_ms=30_000
        )

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert len(windows) == 2

    def test_deterministic_ordering_and_segment_index(self) -> None:
        source = EpisodeSource(frames=[_frame(0), _frame(65_000_000)])
        config = EpisodeSegmentationConfig(
            strategy="fixed_window", fixed_window_duration_ms=30_000
        )

        windows = EpisodeSegmenter().segment(source=source, config=config)

        assert [w.segment_index for w in windows] == list(range(len(windows)))
        starts = [w.start_us for w in windows]
        assert starts == sorted(starts)
        assert len(set(starts)) == len(starts)
