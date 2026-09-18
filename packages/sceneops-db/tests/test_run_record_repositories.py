"""Integration coverage for Scene/Episode run records against real Postgres.

Priority: create/update, per-entity listing, and latest-run selection —
the append-across-executions semantics documented on SceneRunRecordModel /
EpisodeRunRecordModel. created_at is set explicitly on every record here
rather than left to the server_default `now()`, because Postgres resolves
`now()` to transaction-start time — two inserts in the same test
transaction would otherwise tie and make ordering non-deterministic.
"""

from __future__ import annotations

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


@pytest.mark.asyncio
async def test_scene_run_create_and_update(db_session, unique_id):
    repo = PostgresSceneRunRepository(db_session)
    run_id = unique_id("run")
    scene_id = unique_id("scene")

    await repo.create(
        SceneValidationRunRecord(
            run_id=run_id,
            scene_id=scene_id,
            status=RunStatus.RUNNING,
            created_at=_now(),
        )
    )
    fetched = await repo.get(run_id)
    assert fetched.status == RunStatus.RUNNING

    # update() is a full replace (unlike update_scene_summary's None-means-
    # untouched partial contract) — callers must carry the fetched record
    # forward, not construct a fresh partial one, or NOT NULL columns like
    # created_at get blanked. Mirrors the real update-in-place call sites
    # (e.g. validate_scene.py), which always mutate the fetched record.
    await repo.update(
        fetched.model_copy(
            update={"status": RunStatus.SUCCEEDED, "validation_status": "ready"}
        )
    )
    updated = await repo.get(run_id)
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.validation_status == "ready"


@pytest.mark.asyncio
async def test_scene_run_list_filters_by_scene_id(db_session, unique_id):
    repo = PostgresSceneRunRepository(db_session)
    scene_a = unique_id("scene-a")
    scene_b = unique_id("scene-b")

    await repo.create(
        SceneValidationRunRecord(
            run_id=unique_id("run"), scene_id=scene_a, created_at=_now()
        )
    )
    await repo.create(
        SceneValidationRunRecord(
            run_id=unique_id("run"), scene_id=scene_b, created_at=_now()
        )
    )

    result = await repo.list(scene_id=scene_a)
    assert len(result) == 1
    assert result[0].scene_id == scene_a


@pytest.mark.asyncio
async def test_scene_run_latest_by_dataset_version_picks_newest(db_session, unique_id):
    repo = PostgresSceneRunRepository(db_session)
    dataset_id = unique_id("ds")
    dataset_version = "v1"
    scene_id = unique_id("scene")

    older_run_id = unique_id("run-older")
    newer_run_id = unique_id("run-newer")

    await repo.create(
        SceneValidationRunRecord(
            run_id=older_run_id,
            scene_id=scene_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            validation_status="ready",
            created_at=_now(-60),
        )
    )
    await repo.create(
        SceneValidationRunRecord(
            run_id=newer_run_id,
            scene_id=scene_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            validation_status="warning",
            created_at=_now(),
        )
    )

    latest = await repo.list_latest_by_dataset_version(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        run_type=RunType.SCENE_VALIDATION,
    )
    assert latest[scene_id].run_id == newer_run_id
    assert latest[scene_id].validation_status == "warning"


# ── EpisodeRunRecord ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_episode_run_create_and_update(db_session, unique_id):
    repo = PostgresEpisodeRunRepository(db_session)
    run_id = unique_id("run")
    episode_id = unique_id("episode")

    await repo.create(
        EpisodeValidationRunRecord(
            run_id=run_id,
            episode_id=episode_id,
            status=RunStatus.RUNNING,
            created_at=_now(),
        )
    )
    fetched = await repo.get(run_id)
    assert fetched.status == RunStatus.RUNNING

    await repo.update(
        fetched.model_copy(
            update={"status": RunStatus.SUCCEEDED, "validation_status": "ready"}
        )
    )
    updated = await repo.get(run_id)
    assert updated.status == RunStatus.SUCCEEDED
    assert updated.validation_status == "ready"


@pytest.mark.asyncio
async def test_episode_run_list_filters_by_episode_id(db_session, unique_id):
    repo = PostgresEpisodeRunRepository(db_session)
    episode_a = unique_id("episode-a")
    episode_b = unique_id("episode-b")

    await repo.create(
        EpisodeValidationRunRecord(
            run_id=unique_id("run"), episode_id=episode_a, created_at=_now()
        )
    )
    await repo.create(
        EpisodeValidationRunRecord(
            run_id=unique_id("run"), episode_id=episode_b, created_at=_now()
        )
    )

    result = await repo.list(episode_id=episode_a)
    assert len(result) == 1
    assert result[0].episode_id == episode_a


@pytest.mark.asyncio
async def test_episode_run_records_are_append_only_across_executions(
    db_session, unique_id
):
    """Re-validating the same episode (a new job/run_id) must not overwrite
    or remove the prior run row — both must remain independently listable."""
    repo = PostgresEpisodeRunRepository(db_session)
    episode_id = unique_id("episode")

    first_run_id = unique_id("run")
    second_run_id = unique_id("run")

    await repo.create(
        EpisodeValidationRunRecord(
            run_id=first_run_id,
            episode_id=episode_id,
            status=RunStatus.SUCCEEDED,
            created_at=_now(-30),
        )
    )
    await repo.create(
        EpisodeValidationRunRecord(
            run_id=second_run_id,
            episode_id=episode_id,
            status=RunStatus.SUCCEEDED,
            created_at=_now(),
        )
    )

    result = await repo.list(episode_id=episode_id)
    assert {r.run_id for r in result} == {first_run_id, second_run_id}
