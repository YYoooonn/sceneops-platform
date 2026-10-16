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


DATASET_VERSION = "v1"


async def _registered_scene(
    session, unique_id, seed_dataset_version, scene_revision_for, pin_members
):
    """A registered Scene revision (with its manifest ArtifactRecord), pinned
    by a fresh DatasetVersion, so run records can pin a real revision.
    Returns (revision, dataset_id)."""
    from sceneops_db.postgres.scenes import PostgresSceneRepository

    dataset_id = unique_id("ds")
    await seed_dataset_version(session, dataset_id=dataset_id)
    record = await scene_revision_for(session)
    registered = await PostgresSceneRepository(session).register_revision(record)
    await pin_members(session, dataset_id=dataset_id, revisions=[record])
    return registered.revision, dataset_id


def _validation(
    scene,
    run_id,
    *,
    created_at,
    status="ready",
    revision=None,
    run_status=RunStatus.SUCCEEDED,
    dataset_id=None,
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
        dataset_id=dataset_id,
        dataset_version=DATASET_VERSION if dataset_id else None,
        status=run_status,
        validation_status=status,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_scene_run_create_and_update_keeps_revision_pin(
    db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
):
    scene, dataset_id = await _registered_scene(
        db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
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
    db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
):
    scene, dataset_id = await _registered_scene(
        db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
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
async def test_latest_runs_count_only_the_member_revision(
    db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
):
    """A newer run for another revision of the unit must not shadow the runs
    of the revision the DatasetVersion pins, and an unfinished run never
    counts."""
    from sceneops_db.postgres.scenes import PostgresSceneRepository

    scene, dataset_id = await _registered_scene(
        db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
    )
    repo = PostgresSceneRunRepository(db_session)
    old_revision = (scene.manifest_artifact_id, scene.manifest_checksum)

    await repo.create(
        _validation(scene, unique_id("run-a"), created_at=_now(-60), status="ready")
    )
    pinned_older = unique_id("run-b")
    await repo.create(
        _validation(scene, pinned_older, created_at=_now(-30), status="warning")
    )
    await repo.create(
        _validation(
            scene,
            unique_id("run-c"),
            created_at=_now(-10),
            run_status=RunStatus.RUNNING,
        )
    )

    async def latest():
        return await repo.latest_succeeded_for_member_revisions(
            dataset_id=dataset_id,
            dataset_version=DATASET_VERSION,
            run_type=RunType.SCENE_VALIDATION,
        )

    assert (await latest())[scene.scene_id].run_id == pinned_older

    # Another DatasetVersion pins another revision of the unit: there the old
    # revision's runs do not count, even the newest one, until the new
    # revision is validated; the first version still sees its own.
    replacement = await scene_revision_for(
        db_session,
        source=recording_source(
            robot_run_id=scene.robot_run_id, unit_key=scene.unit_key
        ),
        build_config={"channels": ["CAM_FRONT"]},
    )
    assert replacement.scene_id == scene.scene_id
    await PostgresSceneRepository(db_session).register_revision(replacement)
    await seed_dataset_version(db_session, dataset_id=dataset_id, version="v2")
    await pin_members(
        db_session, dataset_id=dataset_id, version="v2", revisions=[replacement]
    )

    async def latest_v2():
        return await repo.latest_succeeded_for_member_revisions(
            dataset_id=dataset_id,
            dataset_version="v2",
            run_type=RunType.SCENE_VALIDATION,
        )

    old_newest = unique_id("run-d")
    await repo.create(
        _validation(scene, old_newest, created_at=_now(), revision=old_revision)
    )
    assert scene.scene_id not in await latest_v2()

    newest = unique_id("run-e")
    await repo.create(
        _validation(replacement, newest, created_at=_now(5), status="failed")
    )
    assert (await latest_v2())[scene.scene_id].run_id == newest
    assert (await latest())[scene.scene_id].run_id == old_newest


@pytest.mark.asyncio
async def test_a_run_record_must_pin_a_registered_revision(
    db_session, unique_id, scene_revision_for
):
    """The full pin references the revision row: a run of an unregistered
    revision, or with an artifact id of another revision, is refused."""
    from sqlalchemy.exc import IntegrityError

    from sceneops_db.postgres.scenes import PostgresSceneRepository

    revision = await scene_revision_for(db_session)
    repo = PostgresSceneRunRepository(db_session)
    with pytest.raises(IntegrityError, match="fk_scene_run_records_revision"):
        async with db_session.begin_nested():
            await repo.create(
                _validation(revision, unique_id("run"), created_at=_now())
            )

    await PostgresSceneRepository(db_session).register_revision(revision)
    await repo.create(_validation(revision, unique_id("run"), created_at=_now()))
    with pytest.raises(IntegrityError, match="fk_scene_run_records_revision"):
        async with db_session.begin_nested():
            await repo.create(
                _validation(
                    revision,
                    unique_id("run"),
                    created_at=_now(),
                    revision=(revision.manifest_artifact_id, "sha256:" + "e" * 64),
                )
            )


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


async def _pinned(db_session, unique_id, seed_dataset_version, episode_revision_for):
    from sceneops_db.postgres.episodes import PostgresEpisodeRepository

    record = await episode_revision_for(db_session)
    await PostgresEpisodeRepository(db_session).register_revision(record)
    return {
        "episode_id": record.episode_id,
        "manifest_artifact_id": record.manifest_artifact_id,
        "manifest_checksum": record.manifest_checksum,
    }


@pytest.mark.asyncio
async def test_episode_run_create_and_update_keeps_the_revision_pin(
    db_session, unique_id, seed_dataset_version, episode_revision_for
):
    repo = PostgresEpisodeRunRepository(db_session)
    pin = await _pinned(
        db_session, unique_id, seed_dataset_version, episode_revision_for
    )
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
    db_session, unique_id, seed_dataset_version, episode_revision_for
):
    """Re-validating the same revision (a new job/run_id) never overwrites or
    removes the prior run row."""
    repo = PostgresEpisodeRunRepository(db_session)
    pin = await _pinned(
        db_session, unique_id, seed_dataset_version, episode_revision_for
    )
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
