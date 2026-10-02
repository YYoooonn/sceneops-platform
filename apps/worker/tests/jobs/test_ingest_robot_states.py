"""Unit tests for IngestRobotStatesJobHandler.

Follows the MagicMock WorkerContext convention used by
test_export_analytics_snapshot.py. RosbagAdapter itself is exercised with a
real synthetic MCAP file (see test_rosbag_raw_log.py) — here we mock
RosbagAdapter to isolate the handler's own orchestration logic (robot/run
lookup, recording-URI fallback, persistence; RobotRuns are never mutated).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from datetime import UTC, datetime

from sceneops_core.artifacts.schemas import ArtifactRecord
from sceneops_core.robots.schemas import (
    MissionRecord,
    MissionStatus,
    RobotRecord,
    RobotRunRecord,
)
from sceneops_worker.jobs.robots.ingest_robot_states import (
    IngestRobotStatesJobHandler,
)


def _robot(robot_id: str = "robot-1") -> RobotRecord:
    return RobotRecord(robot_id=robot_id)


_RECORDING_URI = "/data/artifacts/robot_runs/run-1/recording.mcap"


def _robot_run(run_id: str = "run-1", *, robot_id: str = "robot-1") -> RobotRunRecord:
    return RobotRunRecord(
        run_id=run_id,
        robot_id=robot_id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id=f"art-robotrun-{run_id}",
        manifest_artifact_id=f"art-robotrunmanifest-{run_id}",
        manifest_checksum="sha256:" + "1" * 64,
    )


def _recording_artifact(uri: str = _RECORDING_URI) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id="art-robotrun-run-1",
        kind="robot_run_recording",
        uri=uri,
        checksum="sha256:" + "2" * 64,
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
    recording: ArtifactRecord | None = None,
) -> MagicMock:
    ctx = MagicMock()
    ctx.robot_store = MagicMock()
    ctx.robot_store.get_robot = AsyncMock(return_value=robot)
    ctx.robot_store.get_run = AsyncMock(return_value=robot_run)
    ctx.artifact_record_store.get = AsyncMock(return_value=recording)
    ctx.robot_store.create_states = AsyncMock(side_effect=lambda states: states)
    ctx.robot_store.upsert_mission = AsyncMock(side_effect=lambda mission: mission)
    return ctx


def _mock_adapter(MockAdapter, *, states=None, missions=None) -> None:
    """extract_missions is sync (not awaited), so its mock return value must
    be a plain list — a bare MagicMock() would break `len()`/iteration in
    the handler, unlike AsyncMock-based methods elsewhere in this file."""
    MockAdapter.return_value.extract_robot_states.return_value = states or []
    MockAdapter.return_value.extract_missions.return_value = missions or []


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

    async def test_raises_if_no_source_given(self) -> None:
        ctx = _context(robot=_robot(), robot_run=None)
        handler = IngestRobotStatesJobHandler()

        with pytest.raises(ValueError, match="requires mcap_uri or robot_run_id"):
            await handler.run(_request(_params(robot_run_id=None, mcap_uri=None), ctx))

    async def test_raises_if_recording_artifact_missing(self) -> None:
        ctx = _context(robot=_robot(), robot_run=_robot_run(), recording=None)
        handler = IngestRobotStatesJobHandler()

        with pytest.raises(ValueError, match="missing recording ArtifactRecord"):
            await handler.run(_request(_params(mcap_uri=None), ctx))

    async def test_ingests_states_from_recording_artifact_without_mutating_run(
        self,
    ) -> None:
        robot_run = _robot_run()
        ctx = _context(
            robot=_robot(), robot_run=robot_run, recording=_recording_artifact()
        )
        handler = IngestRobotStatesJobHandler()

        fake_states = _states(3)
        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            _mock_adapter(MockAdapter, states=fake_states)

            result = await handler.run(_request(_params(), ctx))

            MockAdapter.assert_called_once()
            _, kwargs = MockAdapter.call_args
            assert kwargs["source_root_uri"] == _RECORDING_URI

        ctx.artifact_record_store.get.assert_awaited_once_with(
            robot_run.recording_artifact_id
        )
        ctx.robot_store.create_states.assert_awaited_once_with(fake_states)
        # RobotRunRecords are immutable: only reads + state/mission writes.
        assert {call[0] for call in ctx.robot_store.method_calls} <= {
            "get_robot",
            "get_run",
            "create_states",
            "upsert_mission",
        }

        assert result.state_count == 3
        assert result.start_timestamp_us == fake_states[0].timestamp_us
        assert result.end_timestamp_us == fake_states[-1].timestamp_us

    async def test_explicit_mcap_uri_overrides_robot_run(self) -> None:
        robot_run = _robot_run()
        ctx = _context(
            robot=_robot(), robot_run=robot_run, recording=_recording_artifact()
        )
        handler = IngestRobotStatesJobHandler()

        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            _mock_adapter(MockAdapter)
            await handler.run(
                _request(_params(mcap_uri="/explicit/override.mcap"), ctx)
            )

            _, kwargs = MockAdapter.call_args
            assert kwargs["source_root_uri"] == "/explicit/override.mcap"
        ctx.artifact_record_store.get.assert_not_called()

    async def test_no_robot_run_id_skips_run_lookup_and_update(self) -> None:
        ctx = _context(robot=_robot(), robot_run=None)
        handler = IngestRobotStatesJobHandler()

        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            _mock_adapter(MockAdapter)
            result = await handler.run(
                _request(_params(robot_run_id=None, mcap_uri="/standalone.mcap"), ctx)
            )

        ctx.robot_store.get_run.assert_not_called()
        assert result.state_count == 0
        assert result.start_timestamp_us is None
        assert result.end_timestamp_us is None

    async def test_ingests_missions_via_upsert(self) -> None:
        robot_run = _robot_run()
        ctx = _context(
            robot=_robot(), robot_run=robot_run, recording=_recording_artifact()
        )
        handler = IngestRobotStatesJobHandler()

        fake_missions = [
            MissionRecord(
                mission_id="mission-1",
                robot_id="robot-1",
                robot_run_id="run-1",
                status=MissionStatus.COMPLETED,
            )
        ]
        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            _mock_adapter(MockAdapter, missions=fake_missions)

            result = await handler.run(_request(_params(), ctx))

        ctx.robot_store.upsert_mission.assert_awaited_once_with(fake_missions[0])
        assert result.mission_count == 1

    async def test_no_missions_found_upserts_nothing(self) -> None:
        robot_run = _robot_run()
        ctx = _context(
            robot=_robot(), robot_run=robot_run, recording=_recording_artifact()
        )
        handler = IngestRobotStatesJobHandler()

        with patch(
            "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"
        ) as MockAdapter:
            _mock_adapter(MockAdapter)
            result = await handler.run(_request(_params(), ctx))

        ctx.robot_store.upsert_mission.assert_not_called()
        assert result.mission_count == 0
