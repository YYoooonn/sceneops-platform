"""Unit tests for IngestRobotStatesJobHandler.

Follows the MagicMock WorkerContext convention used by
test_export_analytics_snapshot.py. RosbagAdapter itself is exercised with a
real synthetic MCAP file (see test_rosbag_raw_log.py) — here we mock
RosbagAdapter to isolate the handler's own orchestration logic (robot/run
lookup, mcap_uri fallback, persistence, RobotRun status update).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord, RobotRunStatus
from sceneops_worker.jobs.robots.ingest_robot_states import (
    IngestRobotStatesJobHandler,
)


def _robot(robot_id: str = "robot-1") -> RobotRecord:
    return RobotRecord(robot_id=robot_id)


def _robot_run(
    run_id: str = "run-1",
    *,
    robot_id: str = "robot-1",
    mcap_uri: str | None = "/data/raw/rosbag/robot-1/run-1.mcap",
    rosbag_uri: str | None = None,
) -> RobotRunRecord:
    return RobotRunRecord(
        run_id=run_id,
        robot_id=robot_id,
        mcap_uri=mcap_uri,
        rosbag_uri=rosbag_uri,
    )


def _states(count: int, *, robot_id: str = "robot-1", robot_run_id: str = "run-1"):
    from sceneops_core.robots.schemas import RobotStateRecord

    return [
        RobotStateRecord(
            state_id=f"{robot_run_id}-{i}",
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            timestamp_us=1_000_000 + i * 100_000,
        )
        for i in range(count)
    ]


def _request(params: MagicMock, context: MagicMock) -> MagicMock:
    request = MagicMock()
    request.job = MagicMock()
    request.params = params
    request.context = context
    return request


def _params(
    robot_id: str = "robot-1",
    robot_run_id: str | None = "run-1",
    mcap_uri: str | None = None,
) -> MagicMock:
    p = MagicMock()
    p.robot_id = robot_id
    p.robot_run_id = robot_run_id
    p.mcap_uri = mcap_uri
    return p


def _context(
    *,
    robot: RobotRecord | None,
    robot_run: RobotRunRecord | None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.robot_store = MagicMock()
    ctx.robot_store.get_robot = AsyncMock(return_value=robot)
    ctx.robot_store.get_run = AsyncMock(return_value=robot_run)
    ctx.robot_store.save_run = AsyncMock(side_effect=lambda run: run)
    ctx.robot_store.create_states = AsyncMock(side_effect=lambda states: states)
    return ctx


class TestIngestRobotStatesJobHandler:
    async def test_raises_if_robot_not_found(self) -> None:
        ctx = _context(robot=None, robot_run=None)
        handler = IngestRobotStatesJobHandler()

        with pytest.raises(ValueError, match="Robot not found"):
            await handler.run(_request(_params(), ctx))

    async def test_raises_if_robot_run_not_found(self) -> None:
        ctx = _context(robot=_robot(), robot_run=None)
        handler = IngestRobotStatesJobHandler()

        with pytest.raises(ValueError, match="RobotRun not found"):
            await handler.run(_request(_params(robot_run_id="run-1"), ctx))

    async def test_raises_if_no_mcap_uri_resolvable(self) -> None:
        robot_run = _robot_run(mcap_uri=None, rosbag_uri=None)
        ctx = _context(robot=_robot(), robot_run=robot_run)
        handler = IngestRobotStatesJobHandler()

        with pytest.raises(ValueError, match="requires mcap_uri"):
            await handler.run(_request(_params(mcap_uri=None), ctx))

    async def test_ingests_states_and_marks_run_ingested(self) -> None:
        robot_run = _robot_run()
        ctx = _context(robot=_robot(), robot_run=robot_run)
        handler = IngestRobotStatesJobHandler()

        fake_states = _states(3)
        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            MockAdapter.return_value.extract_robot_states.return_value = fake_states

            result = await handler.run(_request(_params(), ctx))

            MockAdapter.assert_called_once()
            _, kwargs = MockAdapter.call_args
            assert kwargs["source_root_uri"] == robot_run.mcap_uri

        ctx.robot_store.create_states.assert_awaited_once_with(fake_states)
        ctx.robot_store.save_run.assert_awaited_once()
        saved_run = ctx.robot_store.save_run.await_args.args[0]
        assert saved_run.status == RobotRunStatus.INGESTED

        assert result.state_count == 3
        assert result.start_timestamp_us == fake_states[0].timestamp_us
        assert result.end_timestamp_us == fake_states[-1].timestamp_us

    async def test_falls_back_to_rosbag_uri_when_mcap_uri_missing(self) -> None:
        robot_run = _robot_run(mcap_uri=None, rosbag_uri="/data/raw/rosbag/run-1.bag")
        ctx = _context(robot=_robot(), robot_run=robot_run)
        handler = IngestRobotStatesJobHandler()

        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            MockAdapter.return_value.extract_robot_states.return_value = []
            await handler.run(_request(_params(), ctx))

            _, kwargs = MockAdapter.call_args
            assert kwargs["source_root_uri"] == "/data/raw/rosbag/run-1.bag"

    async def test_explicit_mcap_uri_overrides_robot_run(self) -> None:
        robot_run = _robot_run(mcap_uri="/from/run.mcap")
        ctx = _context(robot=_robot(), robot_run=robot_run)
        handler = IngestRobotStatesJobHandler()

        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            MockAdapter.return_value.extract_robot_states.return_value = []
            await handler.run(
                _request(_params(mcap_uri="/explicit/override.mcap"), ctx)
            )

            _, kwargs = MockAdapter.call_args
            assert kwargs["source_root_uri"] == "/explicit/override.mcap"

    async def test_no_robot_run_id_skips_run_lookup_and_update(self) -> None:
        ctx = _context(robot=_robot(), robot_run=None)
        handler = IngestRobotStatesJobHandler()

        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            MockAdapter.return_value.extract_robot_states.return_value = []
            result = await handler.run(
                _request(_params(robot_run_id=None, mcap_uri="/standalone.mcap"), ctx)
            )

        ctx.robot_store.get_run.assert_not_called()
        ctx.robot_store.save_run.assert_not_called()
        assert result.state_count == 0
        assert result.start_timestamp_us is None
        assert result.end_timestamp_us is None
