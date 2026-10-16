"""BUILD_RECORDING_SCENES -> REGISTER_SCENES over a local ArtifactStore and
in-memory stores: the producer owns payload + manifest bytes and
ArtifactRecords, retries converge, conflicting bytes fail, and another
segmentation registers other units next to the first. Real PostgreSQL +
MinIO behavior is covered by
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
from sceneops_scenes.artifacts import ObservationPayloadConflictError
from sceneops_scenes.recording_builder import RecordingSceneBuildError

sys.path.insert(0, str(Path(__file__).parent.parent / "scenes"))
from sceneops_recording.testing.ros2_recordings import build_config, default_recording  # noqa: E402

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


async def _register(world, result):
    return await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                manifest_artifact_ids=result.manifest_artifact_ids,
            ),
            context=world.context,
        )
    )


async def test_whole_recording_builds_and_registers_one_scene_sharing_the_payloads(
    world,
):
    """Segmentation is a build policy: the same recording gives one Scene under
    whole_recording, built from the same observation payloads as the fixed
    windows, and a rebuild converges on the registered revision."""
    config = {
        **build_config(),
        "segmentation": {"policy": "whole_recording", "clock": "sensor.header_stamp"},
    }
    result = await _build(world, config)

    assert result.scene_count == 1
    (unit_key,) = result.unit_keys
    assert unit_key.startswith("whole_recording-") and unit_key.endswith("/recording")
    assert result.payload_artifact_count == result.created_payload_count == 8
    await _register(world, result)
    assert [r.unit_key for r in world.scenes.revisions().values()] == [unit_key]

    again = await _build(world, config, "job-2")
    assert again.manifest_artifact_ids == result.manifest_artifact_ids
    assert again.created_payload_count == 0


async def test_build_publishes_payloads_and_manifests_then_registers(world):
    result = await _build(world)

    assert result.scene_count == 2
    # Grid windows 1 and 2 of the 1 s fixed-duration segmentation.
    assert [k.rsplit("/", 1)[1] for k in result.unit_keys] == ["1", "2"]
    assert len({k.rsplit("/", 1)[0] for k in result.unit_keys}) == 1
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
    # Manifests are keyed by unit, not by DatasetVersion.
    assert all("/canonical/scenes/" in m.uri for m in manifests)
    assert all(m.dataset_id is None and m.dataset_version is None for m in manifests)
    # The builder writes no unit, revision or membership.
    assert world.scenes.committed == ({}, {})

    registered = await _register(world, result)
    assert registered.revision_count == 2
    assert {p.manifest_artifact_id for p in registered.revisions} == set(
        result.manifest_artifact_ids
    )
    assert [p.unit_id for p in registered.revisions] == sorted(registered.scene_ids)
    records = list(world.scenes.revisions().values())
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
    assert reregistered.revisions == registered.revisions
    assert reregistered.existing_revision_scene_ids == registered.scene_ids
    assert reregistered.new_unit_scene_ids == []


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


async def test_another_segmentation_is_other_units(world):
    """A coarser segmentation cuts other windows, so it yields other units,
    not revisions of the 1 s ones: registering it adds one new unit and
    leaves the 1 s units and their revisions untouched."""
    first = await _build(world)
    registered = await _register(world, first)

    coarser = await _build(world, build_config(duration_ns=10_000_000_000), "job-2")
    assert coarser.created_payload_count == 0  # payload identity is config-free
    assert not set(coarser.unit_keys) & set(first.unit_keys)

    added = await _register(world, coarser)
    assert len(added.new_unit_scene_ids) == 1
    assert not set(added.scene_ids) & set(registered.scene_ids)
    assert len(world.scenes.revisions()) == 3


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
