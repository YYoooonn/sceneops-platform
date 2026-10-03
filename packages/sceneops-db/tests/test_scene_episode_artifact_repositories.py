"""Integration coverage for EpisodeRecord and ArtifactRecord persistence
against real Postgres. SceneRecord persistence is covered by
test_scene_repository.py."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.episodes.schemas.records import EpisodeRecord
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository


# ── EpisodeRecord ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_episode_record_create_get_round_trip(db_session, unique_id):
    repo = PostgresEpisodeRepository(db_session)
    episode_id = unique_id("episode")
    robot_run_id = unique_id("run")

    await repo.create(
        EpisodeRecord(
            episode_id=episode_id,
            dataset_id=unique_id("ds"),
            dataset_version="v1",
            robot_run_id=robot_run_id,
            frame_count=500,
        )
    )

    fetched = await repo.get(episode_id)
    assert fetched is not None
    assert fetched.robot_run_id == robot_run_id
    assert fetched.frame_count == 500


@pytest.mark.asyncio
async def test_episode_record_list_filters_by_robot_run_id(db_session, unique_id):
    repo = PostgresEpisodeRepository(db_session)
    dataset_id = unique_id("ds")
    run_a = unique_id("run-a")
    run_b = unique_id("run-b")
    episode_a = unique_id("episode-a")
    episode_b = unique_id("episode-b")

    await repo.create(
        EpisodeRecord(
            episode_id=episode_a,
            dataset_id=dataset_id,
            dataset_version="v1",
            robot_run_id=run_a,
        )
    )
    await repo.create(
        EpisodeRecord(
            episode_id=episode_b,
            dataset_id=dataset_id,
            dataset_version="v1",
            robot_run_id=run_b,
        )
    )

    result = await repo.list(robot_run_id=run_a)
    assert [e.episode_id for e in result] == [episode_a]


# ── ArtifactRecord ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_artifact_record_insert_and_get(db_session, unique_id):
    repo = PostgresArtifactRefRepository(db_session)
    artifact_id = generate_artifact_id()
    scene_id = unique_id("scene")
    job_id = unique_id("job")
    pipeline_run_id = unique_id("pipe")

    await repo.create(
        artifact_id=artifact_id,
        ref=ArtifactRef(
            kind=ArtifactKind.SCENE_MANIFEST,
            uri=f"s3://sceneops/artifacts/scenes/{scene_id}.json",
            media_type="application/json",
        ),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=scene_id,
        scene_id=scene_id,
        job_id=job_id,
        pipeline_run_id=pipeline_run_id,
    )

    fetched = await repo.get(artifact_id)
    assert fetched is not None
    assert fetched.kind == ArtifactKind.SCENE_MANIFEST.value
    assert fetched.owner_id == scene_id
    # Lineage fields must be preserved exactly as given.
    assert fetched.job_id == job_id
    assert fetched.pipeline_run_id == pipeline_run_id


@pytest.mark.asyncio
async def test_artifact_record_get_many_returns_only_existing(db_session, unique_id):
    repo = PostgresArtifactRefRepository(db_session)
    ids = [unique_id("art-payload") for _ in range(2)]
    for artifact_id in ids:
        await repo.create(
            artifact_id=artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.OBSERVATION_PAYLOAD,
                uri=f"s3://sceneops/payloads/{artifact_id}",
                media_type="image/jpeg",
                size_bytes=3,
                checksum="sha256:" + "a" * 64,
            ),
        )
    missing = unique_id("art-missing")

    found = await repo.get_many([*ids, missing, ids[0]])

    assert set(found) == set(ids)
    assert all(r.kind == ArtifactKind.OBSERVATION_PAYLOAD.value for r in found.values())
    assert await repo.get_many([]) == {}


@pytest.mark.asyncio
async def test_artifact_record_filter_by_owner(db_session, unique_id):
    repo = PostgresArtifactRefRepository(db_session)
    scene_id = unique_id("scene")
    other_scene_id = unique_id("scene-other")

    await repo.create(
        artifact_id=generate_artifact_id(),
        ref=ArtifactRef(kind=ArtifactKind.SCENE_MANIFEST, uri="s3://x/a.json"),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=scene_id,
        scene_id=scene_id,
    )
    await repo.create(
        artifact_id=generate_artifact_id(),
        ref=ArtifactRef(kind=ArtifactKind.SCENE_MANIFEST, uri="s3://x/b.json"),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=other_scene_id,
        scene_id=other_scene_id,
    )

    result = await repo.list(owner_type=ArtifactOwnerType.SCENE, owner_id=scene_id)
    assert len(result) == 1
    assert result[0].owner_id == scene_id


@pytest.mark.asyncio
async def test_artifact_record_no_dedupe_on_insert(db_session, unique_id):
    """Two creates with the same uri/owner are two distinct rows — the
    insert-only artifact identity model (see Stabilization Request 2 / F-03:
    the fix there was correcting WHO creates the record, not introducing
    dedup at the storage layer)."""
    repo = PostgresArtifactRefRepository(db_session)
    scene_id = unique_id("scene")
    uri = f"s3://sceneops/artifacts/scenes/{scene_id}.json"

    await repo.create(
        artifact_id=generate_artifact_id(),
        ref=ArtifactRef(kind=ArtifactKind.SCENE_MANIFEST, uri=uri),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=scene_id,
        scene_id=scene_id,
    )
    await repo.create(
        artifact_id=generate_artifact_id(),
        ref=ArtifactRef(kind=ArtifactKind.SCENE_MANIFEST, uri=uri),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=scene_id,
        scene_id=scene_id,
    )

    result = await repo.list(owner_type=ArtifactOwnerType.SCENE, owner_id=scene_id)
    assert len(result) == 2
