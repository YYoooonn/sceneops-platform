"""BUILD_RECORDING_SCENES -> REGISTER_SCENES over a local ArtifactStore and
in-memory stores: the producer owns payload + manifest bytes and
ArtifactRecords, retries converge, conflicting bytes fail, and changed
build configuration conflicts or replaces through the registrar. Real
PostgreSQL + MinIO behavior is covered by
tests/scenes/test_recording_scene_vertical_integration.py."""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.jobs.schemas import (
    BuildRecordingScenesJobParams,
    RegisterScenesJobParams,
)
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_recording_scenes import (
    ArtifactRecordConflictError,
    BuildRecordingScenesJobHandler,
)
from sceneops_worker.jobs.dataset.register_scenes import RegisterScenesJobHandler
from sceneops_worker.scenes.artifacts import ObservationPayloadConflictError
from sceneops_worker.scenes.recording_builder import RecordingSceneBuildError
from sceneops_worker.scenes.registration import SceneRegistrationConflictError

sys.path.insert(0, str(Path(__file__).parent.parent / "scenes"))
from recording_fixture import build_config, default_recording  # noqa: E402

RUN_ID = "run-scenes-1"


def _job(job_id="job-1"):
    job = MagicMock()
    job.job_id = job_id
    job.pipeline_run_id = "pipe-1"
    return job


@pytest.fixture()
async def world(scene_world, tmp_path):
    data = default_recording().write(tmp_path / "recording.mcap").read_bytes()
    recording_id = robot_run_recording_artifact_id(RUN_ID)
    uri = f"{scene_world.root}/robot_runs/{RUN_ID}/recording.mcap"
    await scene_world.artifact_store.write_bytes(uri, data)
    checksum = "sha256:" + hashlib.sha256(data).hexdigest()
    scene_world.artifacts[recording_id] = ArtifactRecord(
        artifact_id=recording_id,
        kind=ArtifactKind.ROBOT_RUN_RECORDING,
        uri=uri,
        checksum=checksum,
        size_bytes=len(data),
    )
    scene_world.robot_runs[RUN_ID] = RobotRunRecord(
        run_id=RUN_ID,
        robot_id="robot-1",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        recording_format="mcap",
        source_clock="mcap_log_time",
        recording_artifact_id=recording_id,
        manifest_artifact_id=f"art-robotrunmanifest-{RUN_ID}",
        manifest_checksum="sha256:" + "0" * 64,
    )
    scene_world.add_dataset_version()
    scene_world.context.dataset_store.get_version = AsyncMock(return_value=object())
    scene_world.recording_checksum = checksum
    return scene_world


async def _build(world, config=None, job_id="job-1"):
    return await BuildRecordingScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(job_id),
            params=BuildRecordingScenesJobParams(
                dataset_id="ds",
                dataset_version="v1",
                robot_run_id=RUN_ID,
                build_config=config or build_config(),
            ),
            context=world.context,
        )
    )


async def _register(world, result, *, replace=False):
    return await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                dataset_id="ds",
                dataset_version="v1",
                manifest_artifact_ids=result.manifest_artifact_ids,
                replace=replace,
            ),
            context=world.context,
        )
    )


async def test_whole_recording_builds_and_registers_one_scene_sharing_the_payloads(
    world,
):
    """Segmentation is a build policy: the same recording gives one Scene under
    whole_recording, built from the same observation payloads as the fixed
    windows, and a registered scope converges on a rebuild."""
    config = {
        **build_config(),
        "segmentation": {"policy": "whole_recording", "clock": "sensor.header_stamp"},
    }
    result = await _build(world, config)

    assert result.scene_count == 1
    assert result.unit_keys == ["recording"]
    assert result.payload_artifact_count == result.created_payload_count == 8
    await _register(world, result)
    assert [r.unit_key for r in world.scenes.committed.values()] == ["recording"]

    again = await _build(world, config, "job-2")
    assert again.manifest_artifact_ids == result.manifest_artifact_ids
    assert again.created_payload_count == 0


async def test_build_publishes_payloads_and_manifests_then_registers(world):
    result = await _build(world)

    assert result.scene_count == 2
    assert result.unit_keys == ["segment-000000", "segment-000001"]
    assert result.recording_checksum == world.recording_checksum
    assert result.payload_artifact_count == result.created_payload_count == 8
    payloads = [
        a
        for a in world.artifacts.values()
        if a.kind == ArtifactKind.OBSERVATION_PAYLOAD
    ]
    assert len(payloads) == 8
    for payload in payloads:
        data = await world.artifact_store.read_bytes(payload.uri)
        assert payload.checksum == "sha256:" + hashlib.sha256(data).hexdigest()
        assert payload.owner_type == "robot_run" and payload.owner_id == RUN_ID
        assert f"/observation_payloads/{RUN_ID}/" in payload.uri
    manifests = [world.artifacts[i] for i in result.manifest_artifact_ids]
    assert {m.kind for m in manifests} == {ArtifactKind.SCENE_MANIFEST}
    # The builder writes no membership.
    assert world.scenes.committed == {}

    registered = await _register(world, result)
    assert registered.registered_scene_count == 2
    records = list(world.scenes.committed.values())
    assert {r.robot_run_id for r in records} == {RUN_ID}
    assert {r.unit_key for r in records} == set(result.unit_keys)
    assert {r.window_clock for r in records} == {"sensor.header_stamp"}
    assert {r.manifest_artifact_id for r in records} == set(
        result.manifest_artifact_ids
    )


async def test_retry_converges_on_the_same_artifacts(world):
    first = await _build(world)
    registered = await _register(world, first)
    count = len(world.artifacts)

    again = await _build(world, job_id="job-2")
    assert again.manifest_artifact_ids == first.manifest_artifact_ids
    assert again.created_payload_count == 0
    assert len(world.artifacts) == count
    reregistered = await _register(world, again)
    assert sorted(reregistered.unchanged_scene_ids) == sorted(registered.scene_ids)


async def test_conflicting_payload_bytes_fail_and_are_never_overwritten(world):
    first = await _build(world)
    payload = next(
        a
        for a in world.artifacts.values()
        if a.kind == ArtifactKind.OBSERVATION_PAYLOAD
    )
    await world.artifact_store.write_bytes(payload.uri, b"\xff\xd8\xff tampered")
    with pytest.raises(ObservationPayloadConflictError):
        await _build(world, job_id="job-2")
    assert (
        await world.artifact_store.read_bytes(payload.uri) == b"\xff\xd8\xff tampered"
    )
    assert first.scene_count == 2


async def test_conflicting_artifact_record_fails(world):
    await _build(world)
    payload_id = next(
        i
        for i, a in world.artifacts.items()
        if a.kind == ArtifactKind.OBSERVATION_PAYLOAD
    )
    world.artifacts[payload_id] = world.artifacts[payload_id].model_copy(
        update={"checksum": "sha256:" + "0" * 64}
    )
    with pytest.raises(ArtifactRecordConflictError):
        await _build(world, job_id="job-2")


async def test_changed_config_conflicts_then_replaces(world):
    await _register(world, await _build(world))
    before = dict(world.scenes.committed)

    coarser = await _build(world, build_config(duration_ns=10_000_000_000), "job-2")
    assert coarser.created_payload_count == 0  # payload identity is config-free
    with pytest.raises(SceneRegistrationConflictError):
        await _register(world, coarser)
    assert world.scenes.committed == before

    replaced = await _register(world, coarser, replace=True)
    assert len(replaced.removed_scene_ids) == 1
    assert len(replaced.replaced_scene_ids) == 1
    assert [r.unit_key for r in world.scenes.committed.values()] == ["segment-000000"]


async def test_non_conformant_recording_fails_before_planning(world):
    recording_id = robot_run_recording_artifact_id(RUN_ID)
    record = world.artifacts[recording_id]
    data = (await world.artifact_store.read_bytes(record.uri))[:-40]
    await world.artifact_store.write_bytes(record.uri, data)
    world.artifacts[recording_id] = record.model_copy(
        update={
            "checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    )
    with pytest.raises(RecordingSceneBuildError, match="not L1-conformant"):
        await _build(world)
    assert not any(
        a.kind == ArtifactKind.OBSERVATION_PAYLOAD for a in world.artifacts.values()
    )


def test_params_reject_a_caller_supplied_recording():
    with pytest.raises(ValueError, match="robot_run_id"):
        BuildRecordingScenesJobParams.model_validate(
            {
                "dataset_id": "ds",
                "dataset_version": "v1",
                "robot_run_id": RUN_ID,
                "mcap_uri": "s3://elsewhere/recording.mcap",
                "build_config": build_config(),
            }
        )
