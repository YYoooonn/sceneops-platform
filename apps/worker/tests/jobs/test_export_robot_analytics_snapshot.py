"""Unit tests for ExportRobotAnalyticsSnapshotJobHandler.

Follows the MagicMock WorkerContext convention used by
test_export_analytics_snapshot.py, but returns real RobotStateRecord/
MissionRecord pydantic objects from robot_store since the handler passes
them straight into the real build_robot_telemetry_table/build_missions_table
table builders (not mocked).
"""

from __future__ import annotations

from datetime import UTC, datetime

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_analytics import AnalyticsTableWriter
from sceneops_storage import LocalArtifactStore

from sceneops_core.robots.schemas import (
    MissionRecord,
    MissionStatus,
    RobotRunRecord,
    RobotStateRecord,
)
from sceneops_worker.jobs.robots.export_robot_analytics_snapshot import (
    ExportRobotAnalyticsSnapshotJobHandler,
)
from sceneops_worker.stores.artifacts import ArtifactRecordStore
from tests.derived_harness import FakeArtifactRepo

ROBOT_RUN_ID = "run-1"


def _robot_run() -> RobotRunRecord:
    return RobotRunRecord(
        run_id=ROBOT_RUN_ID,
        robot_id="robot-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id=f"art-robotrun-{ROBOT_RUN_ID}",
        manifest_artifact_id=f"art-robotrunmanifest-{ROBOT_RUN_ID}",
        manifest_checksum="sha256:" + "1" * 64,
    )


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
    root: str = "/unused",
) -> MagicMock:
    ctx = MagicMock()

    ctx.robot_store = MagicMock()
    ctx.robot_store.get_run = AsyncMock(return_value=robot_run)
    ctx.robot_store.list_states = AsyncMock(return_value=states)
    ctx.robot_store.list_missions = AsyncMock(return_value=missions)

    # The real writer over a local store and the real registration logic over
    # an in-memory repository: what is registered is what was written.
    ctx.artifact_store = LocalArtifactStore(root_uri=root)
    ctx.analytics_writer = AnalyticsTableWriter(
        artifact_store=ctx.artifact_store, root_uri=f"{root}/analytical"
    )
    ctx.repo = FakeArtifactRepo()
    records = ArtifactRecordStore.__new__(ArtifactRecordStore)
    records._repo = ctx.repo
    ctx.artifact_record_store = records

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


async def test_exports_both_tables_by_default(tmp_path):
    robot_run = _robot_run()
    states = [_state("s1", timestamp_us=1_000), _state("s2", timestamp_us=2_000)]
    missions = [_mission("mission-1")]
    ctx = _context(
        robot_run=robot_run, states=states, missions=missions, root=str(tmp_path)
    )

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
    assert len(ctx.repo.records) == 2


async def test_respects_requested_table_subset(tmp_path):
    robot_run = _robot_run()
    ctx = _context(
        robot_run=robot_run,
        states=[_state("s1", timestamp_us=1_000)],
        missions=[_mission("mission-1")],
        root=str(tmp_path),
    )

    handler = ExportRobotAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params(tables=["robot_telemetry"])
    request.context = ctx

    result = await handler.run(request)

    assert set(result.table_uris) == {"robot_telemetry"}
    ctx.robot_store.list_missions.assert_not_called()
    assert len(ctx.repo.records) == 1


async def test_empty_states_and_missions_produce_empty_but_valid_tables(tmp_path):
    robot_run = _robot_run()
    ctx = _context(robot_run=robot_run, states=[], missions=[], root=str(tmp_path))

    handler = ExportRobotAnalyticsSnapshotJobHandler()
    request = MagicMock()
    request.job = _job()
    request.params = _params()
    request.context = ctx

    result = await handler.run(request)

    assert result.row_counts == {"robot_telemetry": 0, "missions": 0}
