"""Integration coverage for Episode units, revisions and members and for
ArtifactRecord persistence against real Postgres. Scene persistence, and
the concurrency of revision registration (shared implementation), are
covered by test_scene_repository.py."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.provenance import NonDeterministicProducerError, RevisionDecision
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository


# ── Episode units and revisions ──────────────────────────────────────────────


def _artifact_id() -> str:
    return f"art-test-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_episode_registration_round_trip_and_convergence(
    db_session, episode_revision_for
):
    revision = await episode_revision_for(db_session)
    repo = PostgresEpisodeRepository(db_session)

    first = await repo.register_revision(revision)
    again = await repo.register_revision(revision)

    assert first.decision is RevisionDecision.NEW_UNIT
    assert again.decision is RevisionDecision.EXISTING_REVISION
    assert first.revision.projection() == revision.projection()
    unit = await repo.get(revision.episode_id)
    assert unit.robot_run_id == revision.robot_run_id
    assert await repo.list_revisions(revision.episode_id) == [first.revision]
    assert [
        e.episode_id for e in await repo.list(robot_run_id=revision.robot_run_id)
    ] == [revision.episode_id]


@pytest.mark.asyncio
async def test_episode_revisions_accumulate_and_a_drifting_producer_fails(
    db_session, unique_id, episode_revision_for
):
    run_id = unique_id("run")
    one = await episode_revision_for(db_session, robot_run_id=run_id)
    other_config = await episode_revision_for(
        db_session, robot_run_id=run_id, build_config={"topics": ["/odom"]}
    )
    drifted = await episode_revision_for(db_session, robot_run_id=run_id, x=2.0)
    assert one.episode_id == other_config.episode_id == drifted.episode_id
    assert drifted.producer_fingerprint == one.producer_fingerprint
    repo = PostgresEpisodeRepository(db_session)

    await repo.register_revision(one)
    assert (
        await repo.register_revision(other_config)
    ).decision is RevisionDecision.NEW_REVISION
    with pytest.raises(NonDeterministicProducerError):
        async with db_session.begin_nested():
            await repo.register_revision(drifted)
    assert {r.manifest_checksum for r in await repo.list_revisions(one.episode_id)} == {
        one.manifest_checksum,
        other_config.manifest_checksum,
    }


@pytest.mark.asyncio
async def test_episode_members_pin_revisions_per_dataset_version(
    db_session, unique_id, episode_revision_for, compose_version
):
    dataset_id = unique_id("ds")
    run_id = unique_id("run")
    a = await episode_revision_for(db_session, robot_run_id=run_id, unit_key="a")
    b = await episode_revision_for(db_session, robot_run_id=run_id, unit_key="b")
    repo = PostgresEpisodeRepository(db_session)
    for revision in (a, b):
        await repo.register_revision(revision)
    v1 = await compose_version(
        db_session, dataset_id=dataset_id, version="v1", revisions=[a, b]
    )
    v2 = await compose_version(
        db_session, dataset_id=dataset_id, version="v2", revisions=[a]
    )

    assert (v1.episode.episode_count, v2.episode.episode_count) == (2, 1)
    assert v1.scene is None
    assert [
        e.episode_id
        for e in await repo.list_member_revisions(
            dataset_id=dataset_id, dataset_version="v2", robot_run_id=run_id
        )
    ] == [a.episode_id]
    assert (
        await repo.get_member_revision(
            dataset_id=dataset_id, dataset_version="v2", episode_id=b.episode_id
        )
        is None
    )


@pytest.mark.asyncio
async def test_episode_requires_a_registered_robot_run(
    db_session, episode_revision_for
):
    revision = await episode_revision_for(db_session, seed_run=False)
    with pytest.raises(IntegrityError, match="robot_run_id"):
        async with db_session.begin_nested():
            await PostgresEpisodeRepository(db_session).register_revision(revision)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statement", "error", "match"),
    [
        (
            "UPDATE episode_revisions SET window_end_timestamp_ns = "
            "window_start_timestamp_ns WHERE episode_id = :id",
            DBAPIError,
            "immutable",
        ),
        (
            "UPDATE episodes SET unit_key = 'x' WHERE episode_id = :id",
            DBAPIError,
            "immutable",
        ),
        (
            "DELETE FROM episodes WHERE episode_id = :id",
            IntegrityError,
            "episode_revisions",
        ),
    ],
)
async def test_episode_units_and_revisions_are_never_changed(
    db_session, episode_revision_for, statement, error, match
):
    revision = await episode_revision_for(db_session)
    await PostgresEpisodeRepository(db_session).register_revision(revision)
    with pytest.raises(error, match=match):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": revision.episode_id})


# ── ArtifactRecord ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_artifact_record_insert_and_get(db_session, unique_id):
    repo = PostgresArtifactRefRepository(db_session)
    artifact_id = _artifact_id()
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
        artifact_id=_artifact_id(),
        ref=ArtifactRef(kind=ArtifactKind.SCENE_MANIFEST, uri="s3://x/a.json"),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=scene_id,
        scene_id=scene_id,
    )
    await repo.create(
        artifact_id=_artifact_id(),
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
        artifact_id=_artifact_id(),
        ref=ArtifactRef(kind=ArtifactKind.SCENE_MANIFEST, uri=uri),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=scene_id,
        scene_id=scene_id,
    )
    await repo.create(
        artifact_id=_artifact_id(),
        ref=ArtifactRef(kind=ArtifactKind.SCENE_MANIFEST, uri=uri),
        owner_type=ArtifactOwnerType.SCENE,
        owner_id=scene_id,
        scene_id=scene_id,
    )

    result = await repo.list(owner_type=ArtifactOwnerType.SCENE, owner_id=scene_id)
    assert len(result) == 2
