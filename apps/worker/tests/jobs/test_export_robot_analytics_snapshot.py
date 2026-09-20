"""Unit tests for ExportRobotAnalyticsSnapshotJobHandler.

Follows the MagicMock WorkerContext convention used by
test_export_analytics_snapshot.py, but returns real RobotStateRecord/
MissionRecord pydantic objects from robot_store since the handler passes
them straight into the real build_robot_telemetry_table/build_missions_table
table builders (not mocked).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.robots.schemas import (
    MissionRecord,
    MissionStatus,
    RobotRunRecord,
    RobotStateRecord,
)
from sceneops_worker.jobs.robots.export_robot_analytics_snapshot import (
    ExportRobotAnalyticsSnapshotJobHandler,
)

ROBOT_RUN_ID = "run-1"


def _state(state_id: str, *, timestamp_us: int) -> RobotStateRecord:
    return RobotStateRecord(
        state_id=state_id,
        robot_id="robot-1",
        robot_run_id=ROBOT_RUN_ID,
        timestamp_us=timestamp_us,
        battery=90.0,
    )


def _mission(mission_id: str) -> MissionRecord:
    return MissionRecord(
        mission_id=mission_id,
        robot_id="robot-1",
        robot_run_id=ROBOT_RUN_ID,
        status=MissionStatus.COMPLETED,
    )


def _job(job_id: str = "job-001") -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.pipeline_run_id = None
    return j


def _params(tables: list[str] | None = None) -> MagicMock:
    p = MagicMock()
    p.robot_run_id = ROBOT_RUN_ID
    p.tables = tables
    return p


def _context(
    *,
    robot_run: RobotRunRecord | None,
    states: list[RobotStateRecord],
    missions: list[MissionRecord],
) -> MagicMock:
    ctx = MagicMock()

    ctx.robot_store = MagicMock()
    ctx.robot_store.get_run = AsyncMock(return_value=robot_run)
    ctx.robot_store.list_states = AsyncMock(return_value=states)
    ctx.robot_store.list_missions = AsyncMock(return_value=missions)

    written: dict[str, tuple] = {}

    async def write_robot_run_table(table_name, df, *, robot_run_id):
        uri = f"file:///analytical/robot_runs/{robot_run_id}/{table_name}.parquet"
        written[table_name] = (df, uri)
        return uri

    ctx.analytics_writer = MagicMock()
    ctx.analytics_writer.write_robot_run_table = AsyncMock(
        side_effect=write_robot_run_table
    )
    ctx._written = written

    ctx.artifact_record_store = MagicMock()
    ctx.artifact_record_store.create = AsyncMock(return_value=MagicMock())

    return ctx


async def test_raises_if_robot_run_not_found():
    ctx = _context(robot_run=None, states=[], missions=[])
    handler = ExportRobotAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params()
    request.context = ctx

    with pytest.raises(ValueError, match="RobotRun not found"):
        await handler.run(request)


async def test_exports_both_tables_by_default():
    robot_run = RobotRunRecord(run_id=ROBOT_RUN_ID, robot_id="robot-1")
    states = [_state("s1", timestamp_us=1_000), _state("s2", timestamp_us=2_000)]
    missions = [_mission("mission-1")]
    ctx = _context(robot_run=robot_run, states=states, missions=missions)

    handler = ExportRobotAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params()
    request.context = ctx

    result = await handler.run(request)

    assert set(result.table_uris) == {"robot_telemetry", "missions"}
    assert result.row_counts["robot_telemetry"] == 2
    assert result.row_counts["missions"] == 1
    assert result.robot_run_id == ROBOT_RUN_ID
    assert ctx.artifact_record_store.create.call_count == 2


async def test_respects_requested_table_subset():
    robot_run = RobotRunRecord(run_id=ROBOT_RUN_ID, robot_id="robot-1")
    ctx = _context(
        robot_run=robot_run,
        states=[_state("s1", timestamp_us=1_000)],
        missions=[_mission("mission-1")],
    )

    handler = ExportRobotAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params(tables=["robot_telemetry"])
    request.context = ctx

    result = await handler.run(request)

    assert set(result.table_uris) == {"robot_telemetry"}
    ctx.robot_store.list_missions.assert_not_called()
    assert ctx.artifact_record_store.create.call_count == 1


async def test_empty_states_and_missions_produce_empty_but_valid_tables():
    robot_run = RobotRunRecord(run_id=ROBOT_RUN_ID, robot_id="robot-1")
    ctx = _context(robot_run=robot_run, states=[], missions=[])

    handler = ExportRobotAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params()
    request.context = ctx

    result = await handler.run(request)

    assert result.row_counts == {"robot_telemetry": 0, "missions": 0}
