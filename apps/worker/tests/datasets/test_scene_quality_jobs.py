"""REGISTER_SCENES / VALIDATE_SCENE / PROFILE_SCENE handlers.

Validation and profiling read each Scene at the revision its record pins,
write per-scene run records that pin that revision, and never write a
SceneRecord or DatasetVersion state (ADR-007 §13.4, §17.5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sceneops_core.jobs.schemas import (
    ProfileSceneJobParams,
    RegisterScenesJobParams,
    ValidateSceneJobParams,
)
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes.testing import recording_source
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.profile_scene import ProfileSceneJobHandler
from sceneops_worker.jobs.dataset.register_scenes import RegisterScenesJobHandler
from sceneops_worker.jobs.dataset.validate_scene import (
    ValidateSceneJobHandler,
    _per_scene_validation_run_id,
)
from sceneops_worker.scenes.resolver import (
    InconsistentSceneStateError,
    SceneNotRegisteredError,
)


def _job() -> MagicMock:
    job = MagicMock()
    job.job_id = "job-abc123"
    job.pipeline_run_id = "pipe-xyz"
    job.pipeline_task_run_id = "ptask-001"
    return job


async def _registered(world, *source_keys):
    world.add_dataset_version()
    artifacts = [
        await world.publish(world.manifest(source=recording_source(unit_key=key)))
        for key in source_keys
    ]
    result = await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                dataset_id="ds",
                dataset_version="v1",
                manifest_artifact_ids=[a.artifact_id for a in artifacts],
            ),
            context=world.context,
        )
    )
    return result


async def test_register_handler_reports_canonical_members(scene_world):
    result = await _registered(scene_world, "a", "b")
    assert result.registered_scene_count == 2
    assert result.scene_ids == result.created_scene_ids
    assert set(result.scene_ids) == set(scene_world.scenes.committed)
    assert result.manifest_artifact_ids == [
        scene_world.scenes.committed[s].manifest_artifact_id for s in result.scene_ids
    ]


async def test_validate_pins_the_assessed_revision_and_writes_no_scene_state(
    scene_world,
):
    registered = await _registered(scene_world, "a")
    scene_id = registered.scene_ids[0]
    record_before = scene_world.scenes.committed[scene_id]
    scene_world.context.dataset_store.reset_mock()

    result = await ValidateSceneJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=ValidateSceneJobParams(
                dataset_id="ds",
                dataset_version="v1",
                scene_ids=[scene_id],
                require_target_channels=["CAM_FRONT", "LIDAR_TOP"],
            ),
            context=scene_world.context,
        )
    )

    assert result.status == "ready"
    per_scene = [r for r in scene_world.scene_runs if r.scene_id == scene_id]
    assert len(per_scene) == 1
    run = per_scene[0]
    assert run.run_id == _per_scene_validation_run_id("job-abc123", scene_id)
    assert run.status == RunStatus.SUCCEEDED
    assert run.manifest_artifact_id == record_before.manifest_artifact_id
    assert run.manifest_checksum == record_before.manifest_checksum
    assert (run.checked_observation_count, run.checked_keyframe_count) == (5, 2)
    # Job-level aggregate record carries no pin.
    assert any(
        r.scene_id is None and r.manifest_artifact_id is None
        for r in scene_world.scene_runs
    )
    assert scene_world.scenes.committed[scene_id] == record_before
    assert scene_world.context.dataset_store.method_calls == []


async def test_validate_blocking_result_is_reported_not_written_to_the_record(
    scene_world,
):
    registered = await _registered(scene_world, "a")
    result = await ValidateSceneJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=ValidateSceneJobParams(
                scene_ids=registered.scene_ids, require_target_channels=["RADAR_FRONT"]
            ),
            context=scene_world.context,
        )
    )
    assert result.should_block_pipeline is True
    per_scene = [r for r in scene_world.scene_runs if r.scene_id is not None]
    assert per_scene[0].should_block_pipeline is True
    assert per_scene[0].missing_channel_count == 1
    assert (
        "status"
        not in scene_world.scenes.committed[registered.scene_ids[0]].model_dump()
    )


async def test_validate_fails_loudly_for_unregistered_or_inconsistent_scenes(
    scene_world,
):
    await _registered(scene_world, "a")
    with pytest.raises(SceneNotRegisteredError):
        await ValidateSceneJobHandler().run(
            JobHandlerRequest(
                job=_job(),
                params=ValidateSceneJobParams(scene_ids=["scene-unknown"]),
                context=scene_world.context,
            )
        )

    scene_id = next(iter(scene_world.scenes.committed))
    record = scene_world.scenes.committed[scene_id]
    scene_world.artifacts[record.manifest_artifact_id] = scene_world.artifacts[
        record.manifest_artifact_id
    ].model_copy(update={"checksum": "sha256:" + "f" * 64})
    with pytest.raises(InconsistentSceneStateError):
        await ValidateSceneJobHandler().run(
            JobHandlerRequest(
                job=_job(),
                params=ValidateSceneJobParams(scene_ids=[scene_id]),
                context=scene_world.context,
            )
        )


async def test_validate_without_scenes_reports_a_blocking_empty_input(scene_world):
    result = await ValidateSceneJobHandler().run(
        JobHandlerRequest(
            job=_job(), params=ValidateSceneJobParams(), context=scene_world.context
        )
    )
    assert result.should_block_pipeline is True
    assert result.checked_scene_count == 0


async def test_profile_pins_revision_and_counts_observations(scene_world):
    registered = await _registered(scene_world, "a", "b")
    result = await ProfileSceneJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=ProfileSceneJobParams(scene_ids=registered.scene_ids),
            context=scene_world.context,
        )
    )
    assert result.scene_count == 2
    assert (result.observation_count, result.keyframe_count) == (10, 4)
    assert result.observed_channels == ["CAM_FRONT", "LIDAR_TOP"]

    per_scene = {
        r.scene_id: r for r in scene_world.scene_runs if r.scene_id is not None
    }
    for scene_id in registered.scene_ids:
        record = scene_world.scenes.committed[scene_id]
        assert per_scene[scene_id].assessed(
            manifest_artifact_id=record.manifest_artifact_id,
            manifest_checksum=record.manifest_checksum,
        )
        assert per_scene[scene_id].coverage["observations_by_channel"] == {
            "CAM_FRONT": 3,
            "LIDAR_TOP": 2,
        }


async def test_profile_requires_scene_ids(scene_world):
    with pytest.raises(ValueError, match="scene_id"):
        await ProfileSceneJobHandler().run(
            JobHandlerRequest(
                job=_job(), params=ProfileSceneJobParams(), context=scene_world.context
            )
        )


# ── report artifacts: immutable, content-pinned, convergent ─────────────────


def _report_records(world, kind: str):
    return [r for r in world.artifacts.values() if r.kind == kind]


async def _validate(world, scene_ids, *, channels):
    return await ValidateSceneJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=ValidateSceneJobParams(
                dataset_id="ds",
                dataset_version="v1",
                scene_ids=scene_ids,
                require_target_channels=channels,
            ),
            context=world.context,
        )
    )


async def test_every_validation_report_record_pins_the_bytes_at_its_uri(scene_world):
    from sceneops_core.common.checksums import sha256_checksum

    registered = await _registered(scene_world, "a")
    result = await _validate(scene_world, registered.scene_ids, channels=["CAM_FRONT"])

    reports = _report_records(scene_world, "dataset_validation_report")
    assert len(reports) == 2  # the per-scene report and the run report
    assert result.report_uri in {r.uri for r in reports}
    for record in reports:
        data = await scene_world.artifact_store.read_bytes(record.uri)
        assert record.checksum == sha256_checksum(data)
        assert record.size_bytes == len(data)
        # The key itself names the content it holds.
        assert record.checksum.removeprefix("sha256:") in record.uri
        assert b"created_at" not in data


async def test_re_executing_a_validation_job_converges_on_the_same_reports(
    scene_world,
):
    registered = await _registered(scene_world, "a")
    first = await _validate(scene_world, registered.scene_ids, channels=["CAM_FRONT"])
    ids = {
        r.artifact_id for r in _report_records(scene_world, "dataset_validation_report")
    }

    again = await _validate(scene_world, registered.scene_ids, channels=["CAM_FRONT"])

    assert again.report_uri == first.report_uri
    assert {
        r.artifact_id for r in _report_records(scene_world, "dataset_validation_report")
    } == ids


async def test_a_changed_validation_result_is_a_new_revision_never_an_overwrite(
    scene_world,
):
    registered = await _registered(scene_world, "a")
    first = await _validate(scene_world, registered.scene_ids, channels=["CAM_FRONT"])
    first_bytes = await scene_world.artifact_store.read_bytes(first.report_uri)

    # Same Job and run id, different outcome (a channel the Scene lacks).
    second = await _validate(
        scene_world, registered.scene_ids, channels=["RADAR_FRONT"]
    )

    assert second.report_uri != first.report_uri
    assert await scene_world.artifact_store.read_bytes(first.report_uri) == first_bytes
    uris = {r.uri for r in _report_records(scene_world, "dataset_validation_report")}
    assert {first.report_uri, second.report_uri} <= uris


async def test_re_executing_a_profile_job_converges_and_pins_its_bytes(scene_world):
    from sceneops_core.common.checksums import sha256_checksum

    registered = await _registered(scene_world, "a")

    async def profile():
        return await ProfileSceneJobHandler().run(
            JobHandlerRequest(
                job=_job(),
                params=ProfileSceneJobParams(
                    dataset_id="ds",
                    dataset_version="v1",
                    scene_ids=registered.scene_ids,
                ),
                context=scene_world.context,
            )
        )

    first = await profile()
    records = _report_records(scene_world, "dataset_profile_report")
    again = await profile()

    assert again.report_uri == first.report_uri
    assert _report_records(scene_world, "dataset_profile_report") == records
    for record in records:
        data = await scene_world.artifact_store.read_bytes(record.uri)
        assert record.checksum == sha256_checksum(data)
