"""DatasetVersion persistence against real PostgreSQL (ADR-015 §7, MS-8).

A version row is written only together with all its members, by
``PostgresDatasetVersionRepository.insert``, and never changed afterwards.
The repository has no method that creates an empty version, updates one or
adds a member to an existing one. Every expected failure runs in a
savepoint so the test session stays usable.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from sceneops_core.datasets.composition import composition_checksum
from sceneops_core.datasets.schemas import (
    DatasetRecord,
    DatasetVersionMember,
    DatasetVersionRecord,
    EpisodeVersionSummary,
    RevisionSelector,
    RevisionSourceClause,
    SceneVersionSummary,
)
from sceneops_db.postgres.datasets import (
    PostgresDatasetRepository,
    PostgresDatasetVersionMemberRepository,
    PostgresDatasetVersionRepository,
)
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.repositories import DatasetVersionExistsError
from sceneops_db.session import get_async_sessionmaker


async def _fails(session, exc, match, action):
    with pytest.raises(exc, match=match):
        async with session.begin_nested():
            await action()


def _member(revision) -> DatasetVersionMember:
    return DatasetVersionMember.model_validate(revision.pin().model_dump())


def _record(dataset_id, version="v1", *, revisions, selector=None):
    scenes = [r for r in revisions if hasattr(r, "scene_id")]
    episodes = [r for r in revisions if hasattr(r, "episode_id")]
    return DatasetVersionRecord(
        dataset_id=dataset_id,
        version=version,
        composition_checksum=composition_checksum(r.pin() for r in revisions),
        selector=selector,
        scene=SceneVersionSummary(scene_count=len(scenes)) if scenes else None,
        episode=EpisodeVersionSummary(episode_count=len(episodes))
        if episodes
        else None,
    )


async def _dataset(session, unique_id) -> str:
    dataset_id = unique_id("ds")
    await PostgresDatasetRepository(session).create(
        DatasetRecord(dataset_id=dataset_id)
    )
    return dataset_id


@pytest.mark.asyncio
async def test_insert_writes_the_version_and_all_members_together(
    db_session, unique_id, scene_revision_for, episode_revision_for
):
    dataset_id = await _dataset(db_session, unique_id)
    scene = await scene_revision_for(db_session)
    episode = await episode_revision_for(db_session)
    await PostgresSceneRepository(db_session).register_revision(scene)
    await PostgresEpisodeRepository(db_session).register_revision(episode)
    selector = RevisionSelector(
        sources=[
            RevisionSourceClause(
                domain="scene",
                robot_run_id=scene.robot_run_id,
                producer_fingerprint=scene.producer_fingerprint,
            )
        ]
    )
    record = _record(dataset_id, revisions=[scene, episode], selector=selector)
    repo = PostgresDatasetVersionRepository(db_session)

    committed = await repo.insert(record, [_member(episode), _member(scene)])

    assert committed.composition_checksum == record.composition_checksum
    assert committed.selector == selector
    assert committed.created_at is not None
    assert (committed.scene.scene_count, committed.episode.episode_count) == (1, 1)
    assert await repo.get(dataset_id=dataset_id, version="v1") == committed
    members = await PostgresDatasetVersionMemberRepository(db_session).list(
        dataset_id=dataset_id, version="v1"
    )
    assert sorted(m.unit_id for m in members) == sorted(
        [scene.scene_id, episode.episode_id]
    )
    assert [v.version for v in await repo.list(dataset_id=dataset_id)] == ["v1"]


@pytest.mark.asyncio
async def test_a_name_is_bound_once(db_session, unique_id, scene_revision_for):
    dataset_id = await _dataset(db_session, unique_id)
    one = await scene_revision_for(db_session)
    two = await scene_revision_for(db_session)
    scenes = PostgresSceneRepository(db_session)
    await scenes.register_revision(one)
    await scenes.register_revision(two)
    repo = PostgresDatasetVersionRepository(db_session)
    await repo.insert(_record(dataset_id, revisions=[one]), [_member(one)])

    with pytest.raises(DatasetVersionExistsError):
        await repo.insert(_record(dataset_id, revisions=[two]), [_member(two)])

    # The savepoint kept the session usable and the first composition intact.
    members = await PostgresDatasetVersionMemberRepository(db_session).list(
        dataset_id=dataset_id, version="v1"
    )
    assert [m.unit_id for m in members] == [one.scene_id]


@pytest.mark.asyncio
async def test_no_empty_or_inconsistent_version_is_written(
    db_session, unique_id, scene_revision_for
):
    dataset_id = await _dataset(db_session, unique_id)
    revision = await scene_revision_for(db_session)
    await PostgresSceneRepository(db_session).register_revision(revision)
    repo = PostgresDatasetVersionRepository(db_session)
    record = _record(dataset_id, revisions=[revision])

    with pytest.raises(ValueError, match="at least one member"):
        await repo.insert(record, [])
    with pytest.raises(ValueError, match="summaries count"):
        await repo.insert(
            record.model_copy(update={"scene": SceneVersionSummary(scene_count=2)}),
            [_member(revision)],
        )

    async def empty_row():
        await db_session.execute(
            text(
                "INSERT INTO dataset_versions (id, dataset_id, version, "
                "composition_checksum, scene_count, keyframe_count, "
                "observation_count, episode_count) "
                "VALUES (:id, :d, 'v1', :c, 0, 0, 0, 0)"
            ),
            {
                "id": f"{dataset_id}:v1",
                "d": dataset_id,
                "c": record.composition_checksum,
            },
        )

    await _fails(db_session, IntegrityError, "ck_dataset_versions_counts", empty_row)
    assert await repo.get(dataset_id=dataset_id, version="v1") is None


@pytest.mark.asyncio
async def test_a_committed_version_is_immutable(
    db_session, unique_id, scene_revision_for
):
    dataset_id = await _dataset(db_session, unique_id)
    revision = await scene_revision_for(db_session)
    await PostgresSceneRepository(db_session).register_revision(revision)
    await PostgresDatasetVersionRepository(db_session).insert(
        _record(dataset_id, revisions=[revision]), [_member(revision)]
    )

    async def rebind():
        await db_session.execute(
            text(
                "UPDATE dataset_versions SET composition_checksum = :c "
                "WHERE dataset_id = :d"
            ),
            {"c": "sha256:" + "0" * 64, "d": dataset_id},
        )

    await _fails(db_session, DBAPIError, "immutable", rebind)
    for absent in ("create", "upsert", "update", "delete"):
        assert not hasattr(PostgresDatasetVersionRepository, absent)
    assert not hasattr(PostgresDatasetVersionMemberRepository, "add")


@pytest.mark.asyncio
async def test_concurrent_inserts_of_one_name_one_commits(
    unique_id, scene_revision_for
):
    """The second insert waits on the first's uncommitted key, then fails;
    neither writes a member of the other's composition."""
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        dataset_id = await _dataset(session, unique_id)
        one = await scene_revision_for(session)
        two = await scene_revision_for(session)
        await PostgresSceneRepository(session).register_revision(one)
        await PostgresSceneRepository(session).register_revision(two)
        await session.commit()

    first_wrote = asyncio.Event()
    first_committed = asyncio.Event()

    async def first():
        async with sessionmaker() as session:
            await PostgresDatasetVersionRepository(session).insert(
                _record(dataset_id, revisions=[one]), [_member(one)]
            )
            first_wrote.set()
            await asyncio.sleep(0.5)
            first_committed.set()
            await session.commit()

    async def second():
        await first_wrote.wait()
        async with sessionmaker() as session:
            try:
                await PostgresDatasetVersionRepository(session).insert(
                    _record(dataset_id, revisions=[two]), [_member(two)]
                )
            except DatasetVersionExistsError as exc:
                return exc, first_committed.is_set()
            finally:
                await session.rollback()
        return None, first_committed.is_set()

    _, (error, waited) = await asyncio.gather(first(), second())

    assert isinstance(error, DatasetVersionExistsError)
    assert waited
    async with sessionmaker() as session:
        members = await PostgresDatasetVersionMemberRepository(session).list(
            dataset_id=dataset_id, version="v1"
        )
    assert [m.unit_id for m in members] == [one.scene_id]


@pytest.mark.asyncio
async def test_get_missing_version_returns_none(db_session, unique_id):
    repo = PostgresDatasetVersionRepository(db_session)
    assert await repo.get(dataset_id=unique_id("nonexistent"), version="v1") is None
