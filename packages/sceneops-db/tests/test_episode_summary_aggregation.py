"""Regression coverage for DatasetVersion Episode summary aggregation
against real Postgres.

Reproduces the actual production bug independently of
scripts/canonical/canonical_bootstrap.sh: a caller that registers Episodes
for the same DatasetVersion across several independent operations (as
RegisterEpisodeJobHandler is dispatched once per source scene) must end up
with DatasetVersionRecord.episode.episode_count equal to the true current
canonical membership -- never just the count from whichever operation ran
last. This exercises the real production repositories
(PostgresEpisodeRepository.count / PostgresDatasetVersionRepository.
update_episode_summary), not a reimplementation.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.episodes.schemas.records import EpisodeRecord
from sceneops_core.scenes.testing import external_source
from sceneops_db.models.artifacts import ArtifactModel
from sceneops_db.models.datasets import DatasetModel, DatasetVersionModel
from sceneops_db.models.episodes import EpisodeModel
from sceneops_db.models.scenes import SceneModel
from sceneops_db.postgres.datasets import (
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.session import get_async_sessionmaker


async def _create_dataset(db_session, dataset_id: str) -> None:
    await PostgresDatasetRepository(db_session).create(
        DatasetRecord(dataset_id=dataset_id)
    )


async def _register_one_episode_and_refresh_summary(
    db_session, *, dataset_id: str, version: str, episode_id: str
) -> int:
    """Mirrors RegisterEpisodeJobHandler's own recompute-then-write
    sequence for one independent dispatch: upsert one EpisodeRecord, then
    recompute the live count and write it as the DatasetVersion summary.
    Returns the recomputed count."""
    episode_repo = PostgresEpisodeRepository(db_session)
    version_repo = PostgresDatasetVersionRepository(db_session)

    await episode_repo.upsert(
        EpisodeRecord(
            episode_id=episode_id, dataset_id=dataset_id, dataset_version=version
        )
    )
    count = await episode_repo.count(dataset_id=dataset_id, dataset_version=version)
    await version_repo.update_episode_summary(
        dataset_id=dataset_id, version=version, episode_count=count
    )
    return count


@pytest.mark.asyncio
async def test_independent_registrations_converge_on_true_total(db_session, unique_id):
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
            dataset_id=dataset_id,
            version=version,
            episode_id=episode_id,
        )
        assert count == i

        fetched = await version_repo.get(dataset_id=dataset_id, version=version)
        assert fetched.episode.episode_count == i


@pytest.mark.asyncio
async def test_retry_upsert_of_existing_episode_does_not_inflate_count(
    db_session, unique_id
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
            db_session, dataset_id=dataset_id, version=version, episode_id=episode_id
        )

    # Retry: re-register (upsert) episode "ep-c" again, as a duplicate
    # dispatch/retry would.
    count = await _register_one_episode_and_refresh_summary(
        db_session, dataset_id=dataset_id, version=version, episode_id="ep-c"
    )
    assert count == 3

    fetched = await version_repo.get(dataset_id=dataset_id, version=version)
    assert fetched.episode.episode_count == 3


@pytest.mark.asyncio
async def test_scene_and_episode_domains_stay_independent_while_both_grow(
    db_session, unique_id, scene_record_for
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

    async def _register_scene(source_unit_key: str) -> None:
        await scene_repo.insert(
            await scene_record_for(
                db_session,
                dataset_id=dataset_id,
                dataset_version=version,
                source=external_source(source_unit_key=source_unit_key),
            )
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
        db_session, dataset_id=dataset_id, version=version, episode_id="ep-a"
    )
    await _register_one_episode_and_refresh_summary(
        db_session, dataset_id=dataset_id, version=version, episode_id="ep-b"
    )

    fetched = await version_repo.get(dataset_id=dataset_id, version=version)
    assert fetched.scene.scene_count == 2
    assert fetched.episode.episode_count == 2

    # Add one more Episode -> Scenes must stay 2, Episodes becomes 3.
    await _register_one_episode_and_refresh_summary(
        db_session, dataset_id=dataset_id, version=version, episode_id="ep-c"
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
# own docstring, not relying on timing luck.


async def _register_episode_concurrently(
    *,
    dataset_id: str,
    version: str,
    episode_id: str,
    barrier: asyncio.Barrier,
    use_lock: bool,
) -> None:
    """One independent register_episode-shaped transaction, own session.
    ``use_lock=False`` reproduces the pre-concurrency-fix implementation
    (recompute without locking) to prove the race is real without it."""
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        episode_repo = PostgresEpisodeRepository(session)
        version_repo = PostgresDatasetVersionRepository(session)

        await episode_repo.create(
            EpisodeRecord(
                episode_id=episode_id, dataset_id=dataset_id, dataset_version=version
            )
        )
        # Rendezvous: both transactions have inserted+flushed their own
        # Episode row (visible only to themselves, not yet committed)
        # before either moves on to lock/count/write -- this is what makes
        # the interleaving real rather than incidental.
        await barrier.wait()

        if use_lock:
            await version_repo.lock_for_update(dataset_id=dataset_id, version=version)

        count = await episode_repo.count(dataset_id=dataset_id, dataset_version=version)
        await version_repo.update_episode_summary(
            dataset_id=dataset_id, version=version, episode_count=count
        )
        await session.commit()


async def _cleanup(dataset_id: str) -> None:
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        await session.execute(
            delete(EpisodeModel).where(EpisodeModel.dataset_id == dataset_id)
        )
        await session.execute(
            delete(SceneModel).where(SceneModel.dataset_id == dataset_id)
        )
        await session.execute(
            delete(ArtifactModel).where(ArtifactModel.dataset_id == dataset_id)
        )
        await session.execute(
            delete(DatasetVersionModel).where(
                DatasetVersionModel.dataset_id == dataset_id
            )
        )
        await session.execute(
            delete(DatasetModel).where(DatasetModel.dataset_id == dataset_id)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_registrations_without_lock_lose_an_update(unique_id):
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

    try:
        barrier = asyncio.Barrier(2)
        await asyncio.gather(
            _register_episode_concurrently(
                dataset_id=dataset_id,
                version=version,
                episode_id="ep-a",
                barrier=barrier,
                use_lock=False,
            ),
            _register_episode_concurrently(
                dataset_id=dataset_id,
                version=version,
                episode_id="ep-b",
                barrier=barrier,
                use_lock=False,
            ),
        )

        async with sessionmaker() as verify_session:
            episode_repo = PostgresEpisodeRepository(verify_session)
            version_repo = PostgresDatasetVersionRepository(verify_session)
            live_count = await episode_repo.count(
                dataset_id=dataset_id, dataset_version=version
            )
            fetched = await version_repo.get(dataset_id=dataset_id, version=version)

        assert live_count == 2
        # The lost-update: cached summary is stuck at 1, disagreeing with
        # the true live count of 2.
        assert fetched.episode.episode_count == 1
    finally:
        await _cleanup(dataset_id)


@pytest.mark.asyncio
async def test_concurrent_registrations_with_lock_converge_on_true_total(unique_id):
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

    try:
        barrier = asyncio.Barrier(2)
        await asyncio.gather(
            _register_episode_concurrently(
                dataset_id=dataset_id,
                version=version,
                episode_id="ep-a",
                barrier=barrier,
                use_lock=True,
            ),
            _register_episode_concurrently(
                dataset_id=dataset_id,
                version=version,
                episode_id="ep-b",
                barrier=barrier,
                use_lock=True,
            ),
        )

        async with sessionmaker() as verify_session:
            episode_repo = PostgresEpisodeRepository(verify_session)
            version_repo = PostgresDatasetVersionRepository(verify_session)
            live_count = await episode_repo.count(
                dataset_id=dataset_id, dataset_version=version
            )
            fetched = await version_repo.get(dataset_id=dataset_id, version=version)

        assert live_count == 2
        assert fetched.episode.episode_count == 2
    finally:
        await _cleanup(dataset_id)


async def _register_or_lose_race(
    *, dataset_id: str, version: str, episode_id: str, barrier: asyncio.Barrier
) -> None:
    """Mirrors RegisterEpisodeJobHandler's real get-then-create-or-update
    logic (never a blind create -- see section 4's "do not change Episode
    upsert semantics"). Two concurrent callers racing on the SAME new
    episode_id both read existing=None (barrier forces both reads to
    happen before either writes), so both attempt CREATE -- Postgres's
    primary-key uniqueness constraint rejects whichever commits/flushes
    second with IntegrityError. That rejection (not a graceful upsert) is
    the current, unchanged intended behavior for a true concurrent create
    race on one identity (EpisodeRecord.episode_id is the sole primary
    key); this helper just doesn't propagate that expected failure as a
    test error."""
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        episode_repo = PostgresEpisodeRepository(session)
        version_repo = PostgresDatasetVersionRepository(session)

        existing = await episode_repo.get(episode_id)
        await barrier.wait()

        try:
            if existing is None:
                await episode_repo.create(
                    EpisodeRecord(
                        episode_id=episode_id,
                        dataset_id=dataset_id,
                        dataset_version=version,
                    )
                )
            else:
                await episode_repo.update(existing)
        except IntegrityError:
            await session.rollback()
            return

        await version_repo.lock_for_update(dataset_id=dataset_id, version=version)
        count = await episode_repo.count(dataset_id=dataset_id, dataset_version=version)
        await version_repo.update_episode_summary(
            dataset_id=dataset_id, version=version, episode_count=count
        )
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_retry_upsert_of_same_episode_does_not_inflate_count(
    unique_id,
):
    """Two concurrent dispatches racing to register the SAME NEW episode_id
    (a duplicate/retry scenario, not two distinct episodes) must leave
    canonical Episode rows increased by at most 1, and the cached
    episodeCount matching that live count exactly -- the lock must not
    cause a retry to be double-counted, and losing writer's rejection must
    not leave the summary stale either (the winner's own lock+count+write
    still runs, recomputing against whatever actually committed)."""
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

    try:
        barrier = asyncio.Barrier(2)
        await asyncio.gather(
            _register_or_lose_race(
                dataset_id=dataset_id,
                version=version,
                episode_id="ep-shared",
                barrier=barrier,
            ),
            _register_or_lose_race(
                dataset_id=dataset_id,
                version=version,
                episode_id="ep-shared",
                barrier=barrier,
            ),
        )

        async with sessionmaker() as verify_session:
            episode_repo = PostgresEpisodeRepository(verify_session)
            version_repo = PostgresDatasetVersionRepository(verify_session)
            live_count = await episode_repo.count(
                dataset_id=dataset_id, dataset_version=version
            )
            fetched = await version_repo.get(dataset_id=dataset_id, version=version)

        assert live_count == 1
        assert fetched.episode.episode_count == 1
    finally:
        await _cleanup(dataset_id)


@pytest.mark.asyncio
async def test_concurrent_scene_and_episode_summary_writes_do_not_clobber(
    unique_id, scene_record_for
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
            await scene_repo.insert(
                await scene_record_for(
                    session, dataset_id=dataset_id, dataset_version=version
                )
            )
            await barrier.wait()
            await version_repo.lock_for_update(dataset_id=dataset_id, version=version)
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

    try:
        barrier = asyncio.Barrier(2)
        await asyncio.gather(
            _register_episode_concurrently(
                dataset_id=dataset_id,
                version=version,
                episode_id="ep-a",
                barrier=barrier,
                use_lock=True,
            ),
            _scene_writer(barrier),
        )

        async with sessionmaker() as verify_session:
            fetched = await PostgresDatasetVersionRepository(verify_session).get(
                dataset_id=dataset_id, version=version
            )

        assert fetched.episode.episode_count == 1
        assert fetched.scene.scene_count == 1
    finally:
        await _cleanup(dataset_id)
