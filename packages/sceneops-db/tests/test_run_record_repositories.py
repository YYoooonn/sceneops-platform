"""Integration coverage for Scene/Episode run records against real Postgres.

Priority: create/update, per-entity listing, and latest-run selection —
the append-across-executions semantics documented on SceneRunRecordModel /
EpisodeRunRecordModel. created_at is set explicitly on every record here
rather than left to the server_default `now()`, because Postgres resolves
`now()` to transaction-start time — two inserts in the same test
transaction would otherwise tie and make ordering non-deterministic.
"""

from __future__ import annotations

from sceneops_core.scenes.testing import recording_source

from datetime import datetime, timedelta, timezone

import pytest

from sceneops_core.episodes.schemas.runs import EpisodeValidationRunRecord
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_core.scenes.schemas.runs import SceneValidationRunRecord
from sceneops_db.postgres.episodes import PostgresEpisodeRunRepository
from sceneops_db.postgres.scenes import PostgresSceneRunRepository


def _now(offset_seconds: float = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)


# ── SceneRunRecord ────────────────────────────────────────────────────────────


async def _registered_scene(session, unique_id, seed_dataset_version, scene_record_for):
    """A registered SceneRecord (with its DatasetVersion and manifest
    ArtifactRecord) so run records can pin a real revision."""
    from sceneops_db.postgres.scenes import PostgresSceneRepository

    dataset_id = unique_id("ds")
    await seed_dataset_version(session, dataset_id=dataset_id)
    record = await scene_record_for(session, dataset_id=dataset_id)
    return await PostgresSceneRepository(session).insert(record)


def _validation(
    scene,
    run_id,
    *,
    created_at,
    status="ready",
    revision=None,
    run_status=RunStatus.SUCCEEDED,
):
    artifact_id, checksum = revision or (
        scene.manifest_artifact_id,
        scene.manifest_checksum,
    )
    return SceneValidationRunRecord(
        run_id=run_id,
        scene_id=scene.scene_id,
        manifest_artifact_id=artifact_id,
        manifest_checksum=checksum,
        dataset_id=scene.dataset_id,
        dataset_version=scene.dataset_version,
        status=run_status,
        validation_status=status,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_scene_run_create_and_update_keeps_revision_pin(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    scene = await _registered_scene(
        db_session, unique_id, seed_dataset_version, scene_record_for
    )
    repo = PostgresSceneRunRepository(db_session)
    run_id = unique_id("run")

    await repo.create(
        _validation(
            scene, run_id, created_at=_now(), status=None, run_status=RunStatus.RUNNING
        )
    )
    fetched = await repo.get(run_id)
    assert fetched.status == RunStatus.RUNNING
    assert fetched.assessed(
        manifest_artifact_id=scene.manifest_artifact_id,
        manifest_checksum=scene.manifest_checksum,
    )

    # update() is a full replace — callers carry the fetched record forward.
    await repo.update(
        fetched.model_copy(
            update={"status": RunStatus.SUCCEEDED, "validation_status": "ready"}
        )
    )
    updated = await repo.get(run_id)
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.validation_status == "ready"
    assert updated.manifest_artifact_id == scene.manifest_artifact_id


@pytest.mark.asyncio
async def test_scene_run_list_filters_by_scene_and_revision(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    scene = await _registered_scene(
        db_session, unique_id, seed_dataset_version, scene_record_for
    )
    repo = PostgresSceneRunRepository(db_session)
    pinned = await repo.create(_validation(scene, unique_id("run"), created_at=_now()))
    await repo.create(
        SceneValidationRunRecord(run_id=unique_id("run-job"), created_at=_now())
    )

    assert [r.run_id for r in await repo.list(scene_id=scene.scene_id)] == [
        pinned.run_id
    ]
    assert [
        r.run_id
        for r in await repo.list(manifest_artifact_id=scene.manifest_artifact_id)
    ] == [pinned.run_id]


@pytest.mark.asyncio
async def test_latest_runs_count_only_the_current_revision(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    """A newer run for a superseded revision must not shadow the current
    revision's own runs, and an unfinished run never counts."""
    from sceneops_db.postgres.scenes import PostgresSceneRepository

    scene = await _registered_scene(
        db_session, unique_id, seed_dataset_version, scene_record_for
    )
    repo = PostgresSceneRunRepository(db_session)
    old_revision = (scene.manifest_artifact_id, scene.manifest_checksum)

    await repo.create(
        _validation(scene, unique_id("run-a"), created_at=_now(-60), status="ready")
    )
    current_older = unique_id("run-b")
    await repo.create(
        _validation(scene, current_older, created_at=_now(-30), status="warning")
    )
    await repo.create(
        _validation(
            scene,
            unique_id("run-c"),
            created_at=_now(-10),
            run_status=RunStatus.RUNNING,
        )
    )

    latest = await repo.latest_succeeded_for_current_revisions(
        dataset_id=scene.dataset_id,
        dataset_version=scene.dataset_version,
        run_type=RunType.SCENE_VALIDATION,
    )
    assert latest[scene.scene_id].run_id == current_older

    # Replace the Scene's revision: the old runs no longer count, even the
    # newest one, until the new revision is validated.
    replacement = await scene_record_for(
        db_session,
        dataset_id=scene.dataset_id,
        source=recording_source(
            robot_run_id=scene.robot_run_id, unit_key=scene.unit_key
        ),
        keyframe_timestamps_ns=(1_000, 2_000, 3_000),
    )
    await PostgresSceneRepository(db_session).replace_revision(replacement)
    await repo.create(
        _validation(scene, unique_id("run-d"), created_at=_now(), revision=old_revision)
    )
    latest = await repo.latest_succeeded_for_current_revisions(
        dataset_id=scene.dataset_id,
        dataset_version=scene.dataset_version,
        run_type=RunType.SCENE_VALIDATION,
    )
    assert scene.scene_id not in latest

    newest = unique_id("run-e")
    await repo.create(
        _validation(replacement, newest, created_at=_now(5), status="failed")
    )
    latest = await repo.latest_succeeded_for_current_revisions(
        dataset_id=scene.dataset_id,
        dataset_version=scene.dataset_version,
        run_type=RunType.SCENE_VALIDATION,
    )
    assert latest[scene.scene_id].run_id == newest


@pytest.mark.asyncio
async def test_revision_pin_check_constraint(db_session, unique_id):
    """The database refuses a per-scene run without a pin, independently of
    the model-level check."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="ck_scene_run_records_revision_pin"):
        await db_session.execute(
            text(
                "INSERT INTO scene_run_records (run_id, type, status, scene_id) "
                "VALUES (:run_id, 'scene_validation', 'succeeded', 'scene-x')"
            ),
            {"run_id": unique_id("run")},
        )


# ── EpisodeRunRecord ──────────────────────────────────────────────────────────


async def _pinned(db_session, unique_id, seed_dataset_version, episode_record_for):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await episode_record_for(db_session, dataset_id=dataset_id)
    return {
        "episode_id": record.episode_id,
        "manifest_artifact_id": record.manifest_artifact_id,
        "manifest_checksum": record.manifest_checksum,
    }


@pytest.mark.asyncio
async def test_episode_run_create_and_update_keeps_the_revision_pin(
    db_session, unique_id, seed_dataset_version, episode_record_for
):
    repo = PostgresEpisodeRunRepository(db_session)
    pin = await _pinned(db_session, unique_id, seed_dataset_version, episode_record_for)
    run_id = unique_id("run")
    await repo.create(
        EpisodeValidationRunRecord(
            run_id=run_id, status=RunStatus.RUNNING, created_at=_now(), **pin
        )
    )
    fetched = await repo.get(run_id)
    await repo.update(
        fetched.model_copy(
            update={"status": RunStatus.SUCCEEDED, "validation_status": "ready"}
        )
    )
    updated = await repo.get(run_id)
    assert updated.validation_status == "ready"
    assert updated.assessed(
        manifest_artifact_id=pin["manifest_artifact_id"],
        manifest_checksum=pin["manifest_checksum"],
    )
    by_revision = await repo.list(
        episode_id=pin["episode_id"], manifest_artifact_id=pin["manifest_artifact_id"]
    )
    assert [r.run_id for r in by_revision] == [run_id]


@pytest.mark.asyncio
async def test_episode_run_records_are_append_only_across_executions(
    db_session, unique_id, seed_dataset_version, episode_record_for
):
    """Re-validating the same revision (a new job/run_id) never overwrites or
    removes the prior run row."""
    repo = PostgresEpisodeRunRepository(db_session)
    pin = await _pinned(db_session, unique_id, seed_dataset_version, episode_record_for)
    first, second = unique_id("run"), unique_id("run")
    await repo.create(
        EpisodeValidationRunRecord(
            run_id=first, status=RunStatus.SUCCEEDED, created_at=_now(-30), **pin
        )
    )
    await repo.create(
        EpisodeValidationRunRecord(
            run_id=second, status=RunStatus.SUCCEEDED, created_at=_now(), **pin
        )
    )
    result = await repo.list(episode_id=pin["episode_id"])
    assert [r.run_id for r in result] == [second, first]


@pytest.mark.asyncio
async def test_episode_run_revision_pin_is_enforced_by_the_database(
    db_session, unique_id
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="ck_episode_run_records_revision_pin"):
        await db_session.execute(
            text(
                "INSERT INTO episode_run_records (run_id, type, status, episode_id) "
                "VALUES (:run_id, 'episode_validation', 'succeeded', 'episode-x')"
            ),
            {"run_id": unique_id("run")},
        )
