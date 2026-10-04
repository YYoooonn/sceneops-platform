"""Derived dataset index / manifest jobs: built from every registered
SceneRecord (never from pipeline batch input), each entry pinned to the
Scene's current revision, and never writing the DatasetVersion summary."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.jobs.schemas import (
    BuildDatasetManifestJobParams,
    BuildSceneIndexJobParams,
    RegisterScenesJobParams,
)
from sceneops_core.scenes.testing import recording_source
from sceneops_worker.jobs.base import JobHandlerRequest
from sceneops_worker.jobs.dataset.build_dataset_manifest import (
    BuildDatasetManifestJobHandler,
)
from sceneops_worker.jobs.dataset.build_scene_index import BuildSceneIndexJobHandler
from sceneops_worker.jobs.dataset.register_scenes import RegisterScenesJobHandler
from sceneops_worker.scenes.resolver import InconsistentSceneStateError


def _job() -> MagicMock:
    job = MagicMock()
    job.job_id = "job-1"
    job.pipeline_run_id = "pipe-1"
    return job


async def _register(world, *keys, replace=False, run="run-001"):
    if run not in world.robot_runs:
        world.add_robot_run(run, recording_checksum="sha256:" + "1" * 64)
    artifacts = [
        await world.publish(
            world.manifest(
                source=recording_source(robot_run_id=run, unit_key=key),
                keyframe_timestamps_ns=tuple(range(1_000, 1_000 * (i + 2), 1_000)),
            )
        )
        for i, key in enumerate(keys)
    ]
    await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                dataset_id="ds",
                dataset_version="v1",
                manifest_artifact_ids=[a.artifact_id for a in artifacts],
                replace=replace,
            ),
            context=world.context,
        )
    )


@pytest.fixture()
def world(scene_world):
    scene_world.add_dataset_version()
    written = {}

    async def write_manifest(*, dataset_id, dataset_version, manifest):
        written["manifest"] = manifest
        return f"{scene_world.root}/datasets/{dataset_id}/{dataset_version}/dataset_manifest.json"

    scene_world.context.dataset_artifact_store.write_dataset_manifest = AsyncMock(
        side_effect=write_manifest
    )
    scene_world.context.dataset_store.update_scene_inputs = AsyncMock()
    scene_world.written = written
    return scene_world


async def _build_manifest(world):
    return await BuildDatasetManifestJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=BuildDatasetManifestJobParams(dataset_id="ds", dataset_version="v1"),
            context=world.context,
        )
    )


async def test_manifest_indexes_every_registered_scene_at_its_pinned_revision(world):
    # Two RobotRuns: two recording scopes in one DatasetVersion.
    await _register(world, "a")
    await _register(world, "b", "c", run="run-002")

    result = await _build_manifest(world)
    manifest = world.written["manifest"]

    assert result.scene_count == 3
    assert {e.scene_id for e in manifest.scenes} == set(world.scenes.committed)
    for entry in manifest.scenes:
        record = world.scenes.committed[entry.scene_id]
        artifact = world.artifacts[record.manifest_artifact_id]
        assert (
            entry.manifest_artifact_id,
            entry.manifest_checksum,
            entry.manifest_uri,
        ) == (
            record.manifest_artifact_id,
            record.manifest_checksum,
            artifact.uri,
        )
    assert manifest.keyframe_count == sum(e.keyframe_count for e in manifest.scenes)
    assert manifest.observed_channels == ["CAM_FRONT", "LIDAR_TOP"]


async def test_manifest_job_never_writes_the_membership_summary(world):
    await _register(world, "a")
    world.context.dataset_store.replace_scene_membership_summary.reset_mock()

    result = await _build_manifest(world)

    world.context.dataset_store.replace_scene_membership_summary.assert_not_called()
    world.context.dataset_store.update_scene_inputs.assert_awaited_once_with(
        dataset_id="ds", version="v1", manifest_uri=result.dataset_manifest_uri
    )


async def test_rebuild_after_replacement_pins_the_new_revision(world):
    await _register(world, "a")
    first = await _build_manifest(world)
    first_entry = world.written["manifest"].scenes[0]

    artifact = await world.publish(
        world.manifest(
            source=recording_source(unit_key="a"),
            annotations_per_keyframe=3,
            build_config={"channels": ["CAM_FRONT", "LIDAR_TOP"], "revision": 2},
        )
    )
    await RegisterScenesJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=RegisterScenesJobParams(
                dataset_id="ds",
                dataset_version="v1",
                manifest_artifact_ids=[artifact.artifact_id],
                replace=True,
            ),
            context=world.context,
        )
    )
    second = await _build_manifest(world)
    second_entry = world.written["manifest"].scenes[0]

    assert first.scene_count == second.scene_count == 1
    assert second_entry.scene_id == first_entry.scene_id
    assert (
        second_entry.manifest_artifact_id
        == artifact.artifact_id
        != first_entry.manifest_artifact_id
    )


async def test_inconsistent_pin_fails_loudly(world):
    await _register(world, "a")
    record = next(iter(world.scenes.committed.values()))
    del world.artifacts[record.manifest_artifact_id]
    with pytest.raises(InconsistentSceneStateError):
        await _build_manifest(world)


async def test_jobs_fail_without_registered_scenes(world):
    with pytest.raises(ValueError, match="no registered scenes"):
        await _build_manifest(world)
    with pytest.raises(ValueError, match="no registered scenes"):
        await BuildSceneIndexJobHandler().run(
            JobHandlerRequest(
                job=_job(),
                params=BuildSceneIndexJobParams(dataset_id="ds", dataset_version="v1"),
                context=world.context,
            )
        )


async def test_scene_index_written_from_all_registered_scenes(world):
    await _register(world, "a", "b")
    result = await BuildSceneIndexJobHandler().run(
        JobHandlerRequest(
            job=_job(),
            params=BuildSceneIndexJobParams(dataset_id="ds", dataset_version="v1"),
            context=world.context,
        )
    )
    payload = await world.artifact_store.read_json(result.scene_index_uri)
    assert result.scene_count == payload["scene_count"] == 2
    assert {s["scene_id"] for s in payload["scenes"]} == set(world.scenes.committed)
