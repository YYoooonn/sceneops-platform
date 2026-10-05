"""Integration coverage for EpisodeRecord (canonical membership, registrar
writes only) and ArtifactRecord persistence against real Postgres. SceneRecord persistence is covered by
test_scene_repository.py."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository


# ── EpisodeRecord ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_episode_record_insert_get_round_trip(
    db_session, unique_id, seed_dataset_version, episode_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await episode_record_for(db_session, dataset_id=dataset_id)
    repo = PostgresEpisodeRepository(db_session)

    await repo.insert(record)
    fetched = await repo.get(record.episode_id)
    assert fetched is not None and fetched.registered_at is not None
    assert fetched.model_dump(
        exclude={"registered_at", "updated_at"}
    ) == record.model_dump(exclude={"registered_at", "updated_at"})
    assert await repo.count(dataset_id=dataset_id, dataset_version="v1") == 1


@pytest.mark.asyncio
async def test_episode_record_list_and_recording_scope(
    db_session, unique_id, seed_dataset_version, episode_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    repo = PostgresEpisodeRepository(db_session)
    a = await episode_record_for(db_session, dataset_id=dataset_id)
    b = await episode_record_for(db_session, dataset_id=dataset_id)
    for record in (a, b):
        await repo.insert(record)

    assert [e.episode_id for e in await repo.list(robot_run_id=a.robot_run_id)] == [
        a.episode_id
    ]
    scope = await repo.list_recording_scope(
        dataset_id=dataset_id, dataset_version="v1", robot_run_id=b.robot_run_id
    )
    assert [e.episode_id for e in scope] == [b.episode_id]


@pytest.mark.asyncio
async def test_episode_replace_revision_repoints_and_delete_removes(
    db_session, unique_id, seed_dataset_version, episode_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    repo = PostgresEpisodeRepository(db_session)
    first = await episode_record_for(db_session, dataset_id=dataset_id)
    await repo.insert(first)
    second = await episode_record_for(
        db_session, dataset_id=dataset_id, robot_run_id=first.robot_run_id, x=2.0
    )
    assert second.episode_id == first.episode_id

    updated = await repo.replace_revision(second)
    assert updated.manifest_artifact_id == second.manifest_artifact_id
    assert await repo.delete([first.episode_id]) == 1
    assert await repo.get(first.episode_id) is None


@pytest.mark.asyncio
async def test_episode_requires_registered_run_artifact_and_dataset_version(
    db_session, unique_id, seed_dataset_version, episode_record_for
):
    from sqlalchemy.exc import IntegrityError

    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await episode_record_for(db_session, dataset_id=dataset_id, seed_run=False)
    with pytest.raises(IntegrityError):
        await PostgresEpisodeRepository(db_session).insert(record)


@pytest.mark.asyncio
async def test_episode_window_must_be_non_empty_in_the_database(
    db_session, unique_id, seed_dataset_version, episode_record_for
):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await episode_record_for(db_session, dataset_id=dataset_id)
    await PostgresEpisodeRepository(db_session).insert(record)
    with pytest.raises(IntegrityError, match="ck_episodes_segment_window"):
        await db_session.execute(
            text(
                "UPDATE episodes SET window_end_timestamp_ns = window_start_timestamp_ns "
                "WHERE episode_id = :id"
            ),
            {"id": record.episode_id},
        )


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
