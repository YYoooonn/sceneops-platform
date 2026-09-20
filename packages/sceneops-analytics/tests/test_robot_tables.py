from __future__ import annotations

from datetime import datetime, timezone

from sceneops_core.robots.schemas import MissionRecord, MissionStatus, RobotStateRecord

from sceneops_analytics.tables import build_missions_table, build_robot_telemetry_table


def _state(
    state_id: str, *, timestamp_us: int, battery: float | None = None
) -> RobotStateRecord:
    return RobotStateRecord(
        state_id=state_id,
        robot_id="robot-1",
        robot_run_id="run-1",
        timestamp_us=timestamp_us,
        position=[1.0, 2.0, 0.0],
        orientation=[0.0, 0.0, 0.0, 1.0],
        battery=battery,
    )


def test_build_robot_telemetry_table_row_per_state():
    states = [
        _state("s1", timestamp_us=1_000, battery=90.0),
        _state("s2", timestamp_us=2_000, battery=None),
    ]

    df = build_robot_telemetry_table(states)

    assert df.height == 2
    assert df["robot_id"].to_list() == ["robot-1", "robot-1"]
    assert df["timestamp_us"].to_list() == [1_000, 2_000]
    assert df["battery"].to_list() == [90.0, None]
    assert df["position"][0].to_list() == [1.0, 2.0, 0.0]


def test_build_robot_telemetry_table_empty_is_empty_not_error():
    df = build_robot_telemetry_table([])
    assert df.height == 0
    assert "robot_id" in df.columns


def test_build_missions_table_row_per_mission():
    missions = [
        MissionRecord(
            mission_id="mission-1",
            robot_id="robot-1",
            robot_run_id="run-1",
            status=MissionStatus.COMPLETED,
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ended_at=datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
        )
    ]

    df = build_missions_table(missions)

    assert df.height == 1
    assert df["mission_id"].to_list() == ["mission-1"]
    assert df["status"].to_list() == ["completed"]


def test_build_missions_table_empty_is_empty_not_error():
    df = build_missions_table([])
    assert df.height == 0
    assert "mission_id" in df.columns
