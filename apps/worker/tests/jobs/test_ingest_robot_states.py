"""Unit tests for IngestRobotStatesJobHandler.

Follows the MagicMock WorkerContext convention used by
test_export_analytics_snapshot.py. The recording is registered in a real
LocalArtifactStore (``register_local_recording``) and read through the real
verified recording resolver; RosbagAdapter itself is mocked to isolate the
handler's own orchestration (resolver use, robot identity, persistence;
RobotRuns are never mutated). RosbagAdapter is exercised with real MCAP
files in test_rosbag_raw_log.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from sceneops_core.jobs.schemas import IngestRobotStatesJobParams
from sceneops_core.robots.schemas import MissionRecord, MissionStatus, RobotStateRecord
from sceneops_worker.jobs.robots.ingest_robot_states import (
    IngestRobotStatesJobHandler,
)
from sceneops_worker.robots.resolver import (
    RecordingArtifactInconsistentError,
    RobotRunNotFoundError,
)

_RECORDING_BYTES = b"mcap bytes (RosbagAdapter is mocked in this module)"
_ADAPTER = "sceneops_worker.jobs.robots.ingest_robot_states.RosbagAdapter"


def _states(count: int, *, robot_id: str, robot_run_id: str = "run-1"):
    return [
        RobotStateRecord(
            state_id=f"{robot_run_id}-{i}",
            robot_id=robot_id,
            robot_run_id=robot_run_id,
            timestamp_us=1_000_000 + i * 100_000,
        )
        for i in range(count)
    ]


def _request(context: MagicMock, *, robot_run_id: str = "run-1") -> MagicMock:
    request = MagicMock()
    request.job = MagicMock()
    request.params = IngestRobotStatesJobParams(robot_run_id=robot_run_id)
    request.context = context
    return request


def _context() -> MagicMock:
    ctx = MagicMock()
    ctx.robot_store = MagicMock()
    ctx.robot_store.get_run = AsyncMock(return_value=None)
    ctx.artifact_record_store.get = AsyncMock(return_value=None)
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
    async def test_raises_if_robot_run_not_found(self) -> None:
        ctx = _context()

        with patch(_ADAPTER) as MockAdapter:
            with pytest.raises(RobotRunNotFoundError, match="RobotRun not found"):
                await IngestRobotStatesJobHandler().run(_request(ctx))
            MockAdapter.assert_not_called()
        ctx.robot_store.create_states.assert_not_called()
        ctx.robot_store.upsert_mission.assert_not_called()

    async def test_raises_if_recording_artifact_missing(
        self, register_local_recording
    ) -> None:
        ctx = _context()
        await register_local_recording(ctx, _RECORDING_BYTES)
        ctx.artifact_record_store.get = AsyncMock(return_value=None)

        with patch(_ADAPTER) as MockAdapter:
            with pytest.raises(
                RecordingArtifactInconsistentError, match="does not exist"
            ):
                await IngestRobotStatesJobHandler().run(_request(ctx))
            MockAdapter.assert_not_called()
        ctx.robot_store.create_states.assert_not_called()
        ctx.robot_store.upsert_mission.assert_not_called()

    async def test_ingests_from_resolved_recording_as_the_run_s_robot(
        self, register_local_recording
    ) -> None:
        ctx = _context()
        robot_run, artifact = await register_local_recording(
            ctx, _RECORDING_BYTES, robot_id="robot-of-run"
        )

        fake_states = _states(3, robot_id="robot-of-run")
        adapter_paths: list[Path] = []
        with patch(_ADAPTER) as MockAdapter:
            _mock_adapter(MockAdapter, states=fake_states)

            def _capture(**kwargs):
                path = Path(kwargs["source_root_uri"])
                # The resolver's verified copy exists while the adapter reads it.
                assert path.read_bytes() == _RECORDING_BYTES
                adapter_paths.append(path)
                return MockAdapter.return_value

            MockAdapter.side_effect = _capture

            result = await IngestRobotStatesJobHandler().run(_request(ctx))

            adapter = MockAdapter.return_value
            # Robot identity comes from the RobotRunRecord, never the caller.
            adapter.extract_robot_states.assert_called_once_with(
                robot_id="robot-of-run", robot_run_id="run-1"
            )
            adapter.extract_missions.assert_called_once_with(
                robot_id="robot-of-run", robot_run_id="run-1"
            )

        assert len(adapter_paths) == 1
        assert str(adapter_paths[0]) != artifact.uri
        assert not adapter_paths[0].exists()
        ctx.artifact_record_store.get.assert_awaited_once_with(
            robot_run.recording_artifact_id
        )
        ctx.artifact_store.read_bytes.assert_awaited_once_with(artifact.uri)
        ctx.robot_store.create_states.assert_awaited_once_with(fake_states)
        # RobotRunRecords are immutable: only reads + state/mission writes.
        assert {call[0] for call in ctx.robot_store.method_calls} <= {
            "get_run",
            "create_states",
            "upsert_mission",
        }

        assert result.robot_id == "robot-of-run"
        assert result.robot_run_id == "run-1"
        assert result.state_count == 3
        assert result.start_timestamp_us == fake_states[0].timestamp_us
        assert result.end_timestamp_us == fake_states[-1].timestamp_us

    async def test_ingests_missions_via_upsert(self, register_local_recording) -> None:
        ctx = _context()
        await register_local_recording(ctx, _RECORDING_BYTES)

        fake_missions = [
            MissionRecord(
                mission_id="mission-1",
                robot_id="robot-1",
                robot_run_id="run-1",
                status=MissionStatus.COMPLETED,
            )
        ]
        with patch(_ADAPTER) as MockAdapter:
            _mock_adapter(MockAdapter, missions=fake_missions)
            result = await IngestRobotStatesJobHandler().run(_request(ctx))

        ctx.robot_store.upsert_mission.assert_awaited_once_with(fake_missions[0])
        assert result.mission_count == 1

    async def test_no_missions_found_upserts_nothing(
        self, register_local_recording
    ) -> None:
        ctx = _context()
        await register_local_recording(ctx, _RECORDING_BYTES)

        with patch(_ADAPTER) as MockAdapter:
            _mock_adapter(MockAdapter)
            result = await IngestRobotStatesJobHandler().run(_request(ctx))

        ctx.robot_store.upsert_mission.assert_not_called()
        assert result.mission_count == 0
        assert result.start_timestamp_us is None
        assert result.end_timestamp_us is None


# ── job contract: robot_run_id is the only recording and robot identity ────


def test_params_have_no_recording_uri_or_robot_fields() -> None:
    assert not {"mcap_uri", "rosbag_uri", "robot_id"} & set(
        IngestRobotStatesJobParams.model_fields
    )


@pytest.mark.parametrize("field", ["mcap_uri", "rosbag_uri", "mcapUri"])
def test_params_reject_recording_uri(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        IngestRobotStatesJobParams.model_validate(
            {"robot_run_id": "run-1", field: "/data/raw/rosbag/run.mcap"}
        )


@pytest.mark.parametrize("field", ["robot_id", "robotId"])
def test_params_reject_caller_robot_identity(field: str) -> None:
    """A caller cannot relabel a registered recording as another robot's:
    a robot_id in the params fails validation (HTTP 400 at job creation)
    before any decoding or write -- it is neither honored nor ignored."""
    with pytest.raises(ValidationError, match=f"{field} is not accepted"):
        IngestRobotStatesJobParams.model_validate(
            {"robot_run_id": "run-1", field: "robot-b"}
        )


@pytest.mark.parametrize("robot_run_id", [None, ""])
def test_params_require_robot_run_id(robot_run_id) -> None:
    payload = {} if robot_run_id is None else {"robot_run_id": robot_run_id}
    with pytest.raises(ValidationError, match="robot_?[rR]un_?[iI]d"):
        IngestRobotStatesJobParams.model_validate(payload)


def test_params_accept_job_envelope_dataset_fields() -> None:
    """POST /jobs merges the envelope's dataset_id/dataset_version into every
    job's params; the caller-identity guard must not reject them."""
    params = IngestRobotStatesJobParams.model_validate(
        {
            "robot_run_id": "run-1",
            "dataset_id": "nuscenes",
            "dataset_version": "v1.0-mini",
        }
    )
    assert params.robot_run_id == "run-1"
