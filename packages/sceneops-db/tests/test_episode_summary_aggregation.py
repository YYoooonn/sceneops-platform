"""Regression coverage for DatasetVersion Episode summary aggregation
against real Postgres.

Reproduces the actual production bug independently of
tools/baselines/canonical/canonical_bootstrap.sh: a caller that registers Episodes
for the same DatasetVersion across several independent operations (one
REGISTER_EPISODES per RobotRun) must end up
with DatasetVersionRecord.episode.episode_count equal to the true current
canonical membership (the Episode revisions the version pins) -- never just
the count from whichever operation ran last. This exercises the real
production repositories (PostgresEpisodeRepository.count_members /
PostgresDatasetVersionRepository.update_episode_summary), not a
reimplementation.
"""

from __future__ import annotations

import asyncio

import pytest

from sceneops_core.datasets.schemas import DatasetVersionMember
from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.scenes.testing import recording_source
from sceneops_db.postgres.datasets import (
    PostgresDatasetRepository,
    PostgresDatasetVersionMemberRepository,
    PostgresDatasetVersionRepository,
)
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.session import get_async_sessionmaker


async def _create_dataset(db_session, dataset_id: str) -> None:
    await PostgresDatasetRepository(db_session).create(
        DatasetRecord(dataset_id=dataset_id)
    )


async def _pin_episode(session, record, *, dataset_id, version) -> None:
    """The registrar's write for one Episode: register its revision
    (additive), then pin it unless the version already does."""
    repo = PostgresEpisodeRepository(session)
    await repo.register_revision(record)
    if (
        await repo.get_member_revision(
            dataset_id=dataset_id, dataset_version=version, episode_id=record.episode_id
        )
        is None
    ):
        await PostgresDatasetVersionMemberRepository(session).add(
            dataset_id=dataset_id,
            version=version,
            members=[
                DatasetVersionMember(
                    domain="episode",
                    unit_id=record.episode_id,
                    manifest_artifact_id=record.manifest_artifact_id,
                    manifest_checksum=record.manifest_checksum,
                )
            ],
        )


async def _insert_episode(make, session, *, dataset_id, version, episode_id):
    """Register and pin the Episode with unit key ``episode_id`` of the
    dataset's one RobotRun, unless it already is (the registrar's unchanged
    case)."""
    record = await make(session, robot_run_id=f"run-{dataset_id}", unit_key=episode_id)
    await _pin_episode(session, record, dataset_id=dataset_id, version=version)


async def _register_one_episode_and_refresh_summary(
    db_session, make, *, dataset_id: str, version: str, episode_id: str
) -> int:
    """The Episode registrar's recompute-then-write sequence for one
    registration: pin one Episode revision, recompute the live count and
    write it as the DatasetVersion summary. Returns the recomputed count."""
    episode_repo = PostgresEpisodeRepository(db_session)
    version_repo = PostgresDatasetVersionRepository(db_session)

    await _insert_episode(
        make, db_session, dataset_id=dataset_id, version=version, episode_id=episode_id
    )
    count = await episode_repo.count_members(
        dataset_id=dataset_id, dataset_version=version
    )
    await version_repo.update_episode_summary(
        dataset_id=dataset_id, version=version, episode_count=count
    )
    return count


@pytest.mark.asyncio
async def test_independent_registrations_converge_on_true_total(
    db_session, unique_id, episode_revision_for
):
    """SceneOps: three independent register_episode-shaped operations for
    the SAME DatasetVersion (as canonical-bootstrap dispatches once per
    source scene) must leave episode_count == 3, never 1 (the last
    dispatch's own job-local count)."""
    dataset_id = unique_id("ds")
    version = "v0.0"
    await _create_dataset(db_session, dataset_id)
    version_repo = PostgresDatasetVersionRepository(db_session)
    await version_repo.create(
        DatasetVersionRecord(dataset_id=dataset_id, version=version)
    )

    for i, episode_id in enumerate(["ep-a", "ep-b", "ep-c"], start=1):
        count = await _register_one_episode_and_refresh_summary(
            db_session,
            episode_revision_for,
            dataset_id=dataset_id,
            version=version,
            episode_id=episode_id,
        )
        assert count == i

        fetched = await version_repo.get(dataset_id=dataset_id, version=version)
        assert fetched.episode.episode_count == i


@pytest.mark.asyncio
async def test_retry_upsert_of_existing_episode_does_not_inflate_count(
    db_session, unique_id, episode_revision_for
):
    """Re-registering (upserting) an already-registered episode must leave
    the aggregate count unchanged -- proves recompute-from-live-count is
    retry-safe, unlike a blind increment would be."""
    dataset_id = unique_id("ds")
    version = "v0.0"
    await _create_dataset(db_session, dataset_id)
    version_repo = PostgresDatasetVersionRepository(db_session)
    await version_repo.create(
        DatasetVersionRecord(dataset_id=dataset_id, version=version)
    )

    for episode_id in ["ep-a", "ep-b", "ep-c"]:
        await _register_one_episode_and_refresh_summary(
            db_session,
            episode_revision_for,
            dataset_id=dataset_id,
            version=version,
            episode_id=episode_id,
        )

    # Retry: re-register (upsert) episode "ep-c" again, as a duplicate
    # dispatch/retry would.
    count = await _register_one_episode_and_refresh_summary(
        db_session,
        episode_revision_for,
        dataset_id=dataset_id,
        version=version,
        episode_id="ep-c",
    )
    assert count == 3

    fetched = await version_repo.get(dataset_id=dataset_id, version=version)
    assert fetched.episode.episode_count == 3


@pytest.mark.asyncio
async def test_scene_and_episode_domains_stay_independent_while_both_grow(
    db_session, unique_id, scene_revision_for, episode_revision_for, pin_members
):
    """SceneOps: a combined DatasetVersion where both Scene and Episode
    domains grow independently over several operations must never let one
    domain's aggregate refresh disturb the other's."""
    dataset_id = unique_id("ds")
    version = "v0.0"
    await _create_dataset(db_session, dataset_id)
    version_repo = PostgresDatasetVersionRepository(db_session)
    scene_repo = PostgresSceneRepository(db_session)
    await version_repo.create(
        DatasetVersionRecord(dataset_id=dataset_id, version=version)
    )

    scene_run = unique_id("run")

    async def _register_scene(source_unit_key: str) -> None:
        record = await scene_revision_for(
            db_session,
            source=recording_source(robot_run_id=scene_run, unit_key=source_unit_key),
        )
        await scene_repo.register_revision(record)
        await pin_members(
            db_session, dataset_id=dataset_id, version=version, revisions=[record]
        )

    async def _refresh_scene_summary() -> int:
        # Mirrors the Scene registrar: recompute from membership, replace.
        summary = await scene_repo.summarize_membership(
            dataset_id=dataset_id, dataset_version=version
        )
        await version_repo.replace_scene_membership_summary(
            dataset_id=dataset_id,
            version=version,
            scene_count=summary.scene_count,
            keyframe_count=summary.keyframe_count,
            observation_count=summary.observation_count,
            observed_channels=summary.observed_channels,
        )
        return summary.scene_count

    # Scenes = 2, Episodes = 2.
    await _register_scene("scene-a")
    await _register_scene("scene-b")
    assert await _refresh_scene_summary() == 2
    await _register_one_episode_and_refresh_summary(
        db_session,
        episode_revision_for,
        dataset_id=dataset_id,
        version=version,
        episode_id="ep-a",
    )
    await _register_one_episode_and_refresh_summary(
        db_session,
        episode_revision_for,
        dataset_id=dataset_id,
        version=version,
        episode_id="ep-b",
    )

    fetched = await version_repo.get(dataset_id=dataset_id, version=version)
    assert fetched.scene.scene_count == 2
    assert fetched.episode.episode_count == 2

    # Add one more Episode -> Scenes must stay 2, Episodes becomes 3.
    await _register_one_episode_and_refresh_summary(
        db_session,
        episode_revision_for,
        dataset_id=dataset_id,
        version=version,
        episode_id="ep-c",
    )
    fetched = await version_repo.get(dataset_id=dataset_id, version=version)
    assert fetched.scene.scene_count == 2
    assert fetched.episode.episode_count == 3

    # Add one more Scene -> Episodes must stay 3, Scenes becomes 3.
    await _register_scene("scene-c")
    assert await _refresh_scene_summary() == 3
    fetched = await version_repo.get(dataset_id=dataset_id, version=version)
    assert fetched.scene.scene_count == 3
    assert fetched.episode.episode_count == 3


# ── True concurrency: two independent sessions, forced overlap ──────────────
#
# Everything above exercises the aggregation mechanism sequentially -- never
# proving the actual race the concurrency fix closes. These tests use two
# genuinely independent AsyncSessions/transactions (get_async_sessionmaker,
# same pattern as
# test_scene_episode_artifact_repositories.py's own multi-session test) and
# an asyncio.Barrier to force both transactions' Episode upsert to complete
# before either proceeds to lock/count/write -- reproducing exactly the
# interleaving described in PostgresDatasetVersionRepository.lock_for_update's
# own docstring, not relying on timing luck. (A race on one identity is not
# reachable through the registrar, which reads the scope only after taking
# the lock; apps/worker registration integration tests cover it.)


async def _prepare(make, *, dataset_id: str, version: str, episode_id: str):
    """Seed the RobotRun and EPISODE_MANIFEST artifact an Episode needs and
    return its (not inserted) record, committed before any race starts."""
    async with get_async_sessionmaker()() as session:
        record = await make(session, robot_run_id=f"run-{dataset_id}-{episode_id}")
        await session.commit()
    return record, dataset_id, version


async def _register_episode_concurrently(
    *, record, barrier: asyncio.Barrier, use_lock: bool
) -> None:
    record, dataset_id, version = record
    """One independent registration transaction, own session.

    ``use_lock=True`` follows the registrar: lock the DatasetVersion row,
    then insert, recompute and write. The lock must come first: inserting
    takes a FOR KEY SHARE lock on the row through the dataset_versions
    foreign key, which a later FOR UPDATE by a concurrent registration would
    deadlock against. ``use_lock=False`` omits the lock and lets both
    transactions insert before either counts, to prove the lost update."""
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        episode_repo = PostgresEpisodeRepository(session)
        version_repo = PostgresDatasetVersionRepository(session)
        if use_lock:
            await barrier.wait()
            await version_repo.lock_for_update(dataset_id=dataset_id, version=version)
            await _pin_episode(session, record, dataset_id=dataset_id, version=version)
        else:
            await _pin_episode(session, record, dataset_id=dataset_id, version=version)
            # Rendezvous: both rows inserted (each visible only to its own
            # transaction) before either counts and writes.
            await barrier.wait()
        count = await episode_repo.count_members(
            dataset_id=dataset_id, dataset_version=version
        )
        await version_repo.update_episode_summary(
            dataset_id=dataset_id, version=version, episode_count=count
        )
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_registrations_without_lock_lose_an_update(
    unique_id, episode_revision_for
):
    """Reproduces the exact pre-fix race: two concurrent register_episode
    operations for the same DatasetVersion, neither locking the row, both
    compute count=1 (each seeing only its own uncommitted insert) and both
    write 1 -- final cached episodeCount is 1, not the true 2. This is the
    bug the concurrency fix closes; kept as a permanent regression proving
    the race is real, not a strawman."""
    sessionmaker = get_async_sessionmaker()
    dataset_id = unique_id("ds")
    version = "v0.0"
    async with sessionmaker() as session:
        await PostgresDatasetRepository(session).create(
            DatasetRecord(dataset_id=dataset_id)
        )
        await PostgresDatasetVersionRepository(session).create(
            DatasetVersionRecord(dataset_id=dataset_id, version=version)
        )
        await session.commit()

    records = {
        e: await _prepare(
            episode_revision_for, dataset_id=dataset_id, version=version, episode_id=e
        )
        for e in ("ep-a", "ep-b")
    }
    barrier = asyncio.Barrier(2)
    await asyncio.gather(
        _register_episode_concurrently(
            record=records["ep-a"], barrier=barrier, use_lock=False
        ),
        _register_episode_concurrently(
            record=records["ep-b"], barrier=barrier, use_lock=False
        ),
    )

    async with sessionmaker() as verify_session:
        episode_repo = PostgresEpisodeRepository(verify_session)
        version_repo = PostgresDatasetVersionRepository(verify_session)
        live_count = await episode_repo.count_members(
            dataset_id=dataset_id, dataset_version=version
        )
        fetched = await version_repo.get(dataset_id=dataset_id, version=version)

    assert live_count == 2
    # The lost-update: cached summary is stuck at 1, disagreeing with
    # the true live count of 2.
    assert fetched.episode.episode_count == 1


@pytest.mark.asyncio
async def test_concurrent_registrations_with_lock_converge_on_true_total(
    unique_id, episode_revision_for
):
    """The actual fix: same forced interleaving as the test above, but with
    the row lock in place -- both transactions must still converge on the
    true total (2), because the second to reach the lock blocks until the
    first commits, then recomputes against that committed state."""
    sessionmaker = get_async_sessionmaker()
    dataset_id = unique_id("ds")
    version = "v0.0"
    async with sessionmaker() as session:
        await PostgresDatasetRepository(session).create(
            DatasetRecord(dataset_id=dataset_id)
        )
        await PostgresDatasetVersionRepository(session).create(
            DatasetVersionRecord(dataset_id=dataset_id, version=version)
        )
        await session.commit()

    records = {
        e: await _prepare(
            episode_revision_for, dataset_id=dataset_id, version=version, episode_id=e
        )
        for e in ("ep-a", "ep-b")
    }
    barrier = asyncio.Barrier(2)
    await asyncio.gather(
        _register_episode_concurrently(
            record=records["ep-a"], barrier=barrier, use_lock=True
        ),
        _register_episode_concurrently(
            record=records["ep-b"], barrier=barrier, use_lock=True
        ),
    )

    async with sessionmaker() as verify_session:
        episode_repo = PostgresEpisodeRepository(verify_session)
        version_repo = PostgresDatasetVersionRepository(verify_session)
        live_count = await episode_repo.count_members(
            dataset_id=dataset_id, dataset_version=version
        )
        fetched = await version_repo.get(dataset_id=dataset_id, version=version)

    assert live_count == 2
    assert fetched.episode.episode_count == 2


@pytest.mark.asyncio
async def test_concurrent_scene_and_episode_summary_writes_do_not_clobber(
    unique_id, scene_revision_for, episode_revision_for, pin_members
):
    """A genuinely concurrent Scene-domain write and Episode-domain write
    against the SAME DatasetVersion row (different sessions/transactions)
    must both survive -- proves the column-scoped partial-update design
    (values_without_none + per-attribute dirty tracking) is safe under real
    concurrency, not just sequential ordering. Both registrars lock the
    row, recompute from their own membership and write only their own
    domain's columns, so whichever runs second must not lose the first's
    write."""
    sessionmaker = get_async_sessionmaker()
    dataset_id = unique_id("ds")
    version = "v0.0"
    async with sessionmaker() as session:
        await PostgresDatasetRepository(session).create(
            DatasetRecord(dataset_id=dataset_id)
        )
        await PostgresDatasetVersionRepository(session).create(
            DatasetVersionRecord(dataset_id=dataset_id, version=version)
        )
        await session.commit()

    async def _scene_writer(barrier: asyncio.Barrier) -> None:
        async with sessionmaker() as session:
            scene_repo = PostgresSceneRepository(session)
            version_repo = PostgresDatasetVersionRepository(session)
            record = await scene_revision_for(session)
            # Registrar order: lock the DatasetVersion row, then write.
            await barrier.wait()
            await version_repo.lock_for_update(dataset_id=dataset_id, version=version)
            await scene_repo.register_revision(record)
            await pin_members(
                session, dataset_id=dataset_id, version=version, revisions=[record]
            )
            summary = await scene_repo.summarize_membership(
                dataset_id=dataset_id, dataset_version=version
            )
            await version_repo.replace_scene_membership_summary(
                dataset_id=dataset_id,
                version=version,
                scene_count=summary.scene_count,
                keyframe_count=summary.keyframe_count,
                observation_count=summary.observation_count,
                observed_channels=summary.observed_channels,
            )
            await session.commit()

    records = {
        e: await _prepare(
            episode_revision_for, dataset_id=dataset_id, version=version, episode_id=e
        )
        for e in ("ep-a",)
    }
    barrier = asyncio.Barrier(2)
    await asyncio.gather(
        _register_episode_concurrently(
            record=records["ep-a"], barrier=barrier, use_lock=True
        ),
        _scene_writer(barrier),
    )

    async with sessionmaker() as verify_session:
        fetched = await PostgresDatasetVersionRepository(verify_session).get(
            dataset_id=dataset_id, version=version
        )

    assert fetched.episode.episode_count == 1
    assert fetched.scene.scene_count == 1
