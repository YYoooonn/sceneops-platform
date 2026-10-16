"""Scene unit / revision / membership persistence against real PostgreSQL
(ADR-015 §3, §6.4, §7; MS-6, MS-7).

    scenes                     the unit: id + source binding; one row per
                               source unit; never updated
    scene_revisions            (scene_id, manifest_checksum); one checksum per
                               (unit, producer fingerprint); never updated
    dataset_version_members    a DatasetVersion's pin of exactly one
                               registered revision per unit; never updated

``register_revision`` applies ``decide_revision_registration`` inside the
caller's transaction and only adds rows; concurrent registrations are
decided by the unique keys. Every expected failure runs in a savepoint so
the test session stays usable.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from sceneops_core.datasets.schemas import DatasetVersionMember
from sceneops_core.provenance import (
    InconsistentRevisionsError,
    NonDeterministicProducerError,
    RevisionDecision,
)
from sceneops_core.scenes.testing import recording_source
from sceneops_db.postgres.datasets import PostgresDatasetVersionMemberRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.repositories import UnitIdentityConflictError
from sceneops_db.session import get_async_sessionmaker


async def _fails(session, exc, match, action):
    """Run ``action`` in a savepoint and require it to raise."""
    with pytest.raises(exc, match=match):
        async with session.begin_nested():
            await action()


# --- units and revisions -----------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_needs_no_dataset_version_and_round_trips(
    db_session, scene_revision_for
):
    revision = await scene_revision_for(db_session)
    repo = PostgresSceneRepository(db_session)

    registered = await repo.register_revision(revision)

    assert registered.decision is RevisionDecision.NEW_UNIT
    assert registered.revision.projection() == revision.projection()
    assert registered.revision.registered_at is not None
    unit = await repo.get(revision.scene_id)
    assert unit is not None and unit.registered_at is not None
    assert unit.model_dump(exclude={"registered_at"}) == revision.unit().model_dump(
        exclude={"registered_at"}
    )
    assert (
        await repo.get_revision(
            scene_id=revision.scene_id, manifest_checksum=revision.manifest_checksum
        )
        == registered.revision
    )
    assert [
        s.scene_id for s in await repo.list(robot_run_id=revision.robot_run_id)
    ] == [revision.scene_id]


@pytest.mark.asyncio
async def test_identical_registration_converges_on_one_row(
    db_session, scene_revision_for
):
    revision = await scene_revision_for(db_session)
    repo = PostgresSceneRepository(db_session)
    first = await repo.register_revision(revision)

    again = await repo.register_revision(revision)

    assert again.decision is RevisionDecision.EXISTING_REVISION
    assert again.revision == first.revision
    assert await repo.list_revisions(revision.scene_id) == [first.revision]


@pytest.mark.asyncio
async def test_a_unit_holds_several_immutable_revisions(
    db_session, unique_id, scene_revision_for
):
    source = recording_source(robot_run_id=unique_id("run"))
    one = await scene_revision_for(db_session, source=source)
    two = await scene_revision_for(
        db_session, source=source, build_config={"channels": ["CAM_FRONT"]}
    )
    assert one.scene_id == two.scene_id
    assert one.producer_fingerprint != two.producer_fingerprint
    repo = PostgresSceneRepository(db_session)

    assert (await repo.register_revision(one)).decision is RevisionDecision.NEW_UNIT
    assert (await repo.register_revision(two)).decision is RevisionDecision.NEW_REVISION

    revisions = await repo.list_revisions(one.scene_id)
    assert {r.manifest_checksum for r in revisions} == {
        one.manifest_checksum,
        two.manifest_checksum,
    }
    assert len(await repo.list(robot_run_id=source.robot_run_id)) == 1


@pytest.mark.asyncio
async def test_a_second_checksum_for_one_fingerprint_is_refused(
    db_session, unique_id, scene_revision_for
):
    source = recording_source(robot_run_id=unique_id("run"))
    one = await scene_revision_for(db_session, source=source)
    drifted = await scene_revision_for(
        db_session, source=source, keyframe_timestamps_ns=(1_000,)
    )
    assert drifted.producer_fingerprint == one.producer_fingerprint
    assert drifted.manifest_checksum != one.manifest_checksum
    repo = PostgresSceneRepository(db_session)
    await repo.register_revision(one)

    await _fails(
        db_session,
        NonDeterministicProducerError,
        "not deterministic",
        lambda: repo.register_revision(drifted),
    )

    # The database refuses it on its own, whoever writes it.
    async def direct_insert():
        await db_session.execute(
            text(
                "INSERT INTO scene_revisions (scene_id, manifest_checksum, "
                "manifest_artifact_id, producer_fingerprint, window_clock, "
                "window_start_timestamp_ns, window_end_timestamp_ns, "
                "observation_count, keyframe_count) VALUES (:s, :c, :a, :f, "
                "'mcap_log_time', 0, 1, 0, 0)"
            ),
            {
                "s": drifted.scene_id,
                "c": drifted.manifest_checksum,
                "a": drifted.manifest_artifact_id,
                "f": drifted.producer_fingerprint,
            },
        )

    await _fails(
        db_session, IntegrityError, "uq_scene_revisions_fingerprint", direct_insert
    )
    assert len(await repo.list_revisions(one.scene_id)) == 1


@pytest.mark.asyncio
async def test_a_registered_revision_with_another_projection_is_inconsistent(
    db_session, scene_revision_for
):
    revision = await scene_revision_for(db_session)
    repo = PostgresSceneRepository(db_session)
    await repo.register_revision(revision)
    lying = revision.model_copy(update={"observation_count": 99})

    await _fails(
        db_session,
        InconsistentRevisionsError,
        "projection",
        lambda: repo.register_revision(lying),
    )


@pytest.mark.asyncio
async def test_one_source_unit_has_exactly_one_unit_row(
    db_session, unique_id, scene_revision_for
):
    """A writer that derived another id for a registered source unit cannot
    fork its identity: (robot_run_id, unit_key) is unique."""
    revision = await scene_revision_for(
        db_session, source=recording_source(robot_run_id=unique_id("run"))
    )
    await db_session.execute(
        text(
            "INSERT INTO scenes (scene_id, source_kind, robot_run_id, unit_key) "
            "VALUES ('scene-foreign-id', 'recording', :r, :k)"
        ),
        {"r": revision.robot_run_id, "k": revision.unit_key},
    )
    repo = PostgresSceneRepository(db_session)
    await _fails(
        db_session,
        UnitIdentityConflictError,
        "registered under an id other than",
        lambda: repo.register_revision(revision),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO scenes (scene_id, source_kind, robot_run_id, unit_key) "
            "VALUES ('scene-x', 'content', :run, 'k')",
            "ck_scenes_source_kind",
        ),
        (
            "INSERT INTO scenes (scene_id, source_kind, robot_run_id, unit_key) "
            "VALUES ('scene-x', 'recording', 'run-not-registered', 'k')",
            "robot_run_id",
        ),
        (
            "INSERT INTO scene_revisions (scene_id, manifest_checksum, "
            "manifest_artifact_id, producer_fingerprint, window_clock, "
            "window_start_timestamp_ns, window_end_timestamp_ns, observation_count, "
            "keyframe_count) VALUES (:scene, :checksum, 'art-not-registered', "
            ":checksum, 'c', 0, 1, 0, 0)",
            "manifest_artifact_id",
        ),
        (
            "INSERT INTO scene_revisions (scene_id, manifest_checksum, "
            "manifest_artifact_id, producer_fingerprint, window_clock, "
            "window_start_timestamp_ns, window_end_timestamp_ns, observation_count, "
            "keyframe_count) VALUES (:scene, 'not-a-checksum', :artifact, "
            ":checksum, 'c', 0, 1, 0, 0)",
            "ck_scene_revisions_checksums",
        ),
        (
            "INSERT INTO scene_revisions (scene_id, manifest_checksum, "
            "manifest_artifact_id, producer_fingerprint, window_clock, "
            "window_start_timestamp_ns, window_end_timestamp_ns, observation_count, "
            "keyframe_count) VALUES (:scene, :other, :artifact, :other, 'c', 5, 5, "
            "0, 0)",
            "ck_scene_revisions_segment_window",
        ),
    ],
)
async def test_unit_and_revision_constraints(
    db_session, scene_revision_for, statement, constraint
):
    revision = await scene_revision_for(db_session)
    await PostgresSceneRepository(db_session).register_revision(revision)

    async def insert():
        await db_session.execute(
            text(statement),
            {
                "run": revision.robot_run_id,
                "scene": revision.scene_id,
                "checksum": revision.manifest_checksum,
                "artifact": revision.manifest_artifact_id,
                "other": "sha256:" + "e" * 64,
            },
        )

    await _fails(db_session, IntegrityError, constraint, insert)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE scenes SET unit_key = 'moved' WHERE scene_id = :scene",
        "UPDATE scene_revisions SET observation_count = 0 WHERE scene_id = :scene",
        "UPDATE dataset_version_members SET manifest_checksum = manifest_checksum "
        "WHERE unit_id = :scene",
    ],
)
async def test_units_revisions_and_members_are_immutable(
    db_session,
    unique_id,
    seed_dataset_version,
    scene_revision_for,
    pin_members,
    statement,
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    revision = await scene_revision_for(db_session)
    await PostgresSceneRepository(db_session).register_revision(revision)
    await pin_members(db_session, dataset_id=dataset_id, revisions=[revision])

    async def update():
        await db_session.execute(text(statement), {"scene": revision.scene_id})

    await _fails(db_session, DBAPIError, "immutable", update)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("DELETE FROM scenes WHERE scene_id = :scene", "scene_revisions"),
        (
            "DELETE FROM scene_revisions WHERE scene_id = :scene",
            "fk_dataset_version_members_scene_revision",
        ),
        ("DELETE FROM artifacts WHERE artifact_id = :artifact", "scene_revisions"),
        (
            "DELETE FROM dataset_versions WHERE dataset_id = :dataset",
            "dataset_version_members",
        ),
    ],
)
async def test_referenced_rows_cannot_be_deleted(
    db_session,
    unique_id,
    seed_dataset_version,
    scene_revision_for,
    pin_members,
    statement,
    constraint,
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    revision = await scene_revision_for(db_session)
    await PostgresSceneRepository(db_session).register_revision(revision)
    await pin_members(db_session, dataset_id=dataset_id, revisions=[revision])

    async def delete():
        await db_session.execute(
            text(statement),
            {
                "scene": revision.scene_id,
                "artifact": revision.manifest_artifact_id,
                "dataset": dataset_id,
            },
        )

    await _fails(db_session, IntegrityError, constraint, delete)


# --- DatasetVersion membership --------------------------------------------------


@pytest.mark.asyncio
async def test_one_revision_is_pinned_by_two_dataset_versions(
    db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id, version="v1")
    await seed_dataset_version(db_session, dataset_id=dataset_id, version="v2")
    revision = await scene_revision_for(db_session)
    repo = PostgresSceneRepository(db_session)
    await repo.register_revision(revision)

    for version in ("v1", "v2"):
        await pin_members(
            db_session, dataset_id=dataset_id, version=version, revisions=[revision]
        )

    for version in ("v1", "v2"):
        assert await repo.list_member_revisions(
            dataset_id=dataset_id, dataset_version=version
        ) == [
            await repo.get_revision(
                scene_id=revision.scene_id, manifest_checksum=revision.manifest_checksum
            )
        ]
    assert len(await repo.list_revisions(revision.scene_id)) == 1


@pytest.mark.asyncio
async def test_a_version_pins_at_most_one_revision_per_unit(
    db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    source = recording_source(robot_run_id=unique_id("run"))
    one = await scene_revision_for(db_session, source=source)
    two = await scene_revision_for(
        db_session, source=source, build_config={"channels": ["CAM_FRONT"]}
    )
    repo = PostgresSceneRepository(db_session)
    await repo.register_revision(one)
    await repo.register_revision(two)
    await pin_members(db_session, dataset_id=dataset_id, revisions=[one])

    await _fails(
        db_session,
        IntegrityError,
        "dataset_version_members_pkey",
        lambda: pin_members(db_session, dataset_id=dataset_id, revisions=[two]),
    )
    pinned = await repo.get_member_revision(
        dataset_id=dataset_id, dataset_version="v1", scene_id=one.scene_id
    )
    assert pinned.manifest_checksum == one.manifest_checksum


@pytest.mark.asyncio
async def test_a_member_must_name_a_registered_revision(
    db_session, unique_id, seed_dataset_version, scene_revision_for
):
    """The foreign key covers the full pin: an artifact id and checksum that
    name no registered revision of the unit are refused."""
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    revision = await scene_revision_for(db_session)
    members = PostgresDatasetVersionMemberRepository(db_session)
    pin = DatasetVersionMember(
        domain="scene",
        unit_id=revision.scene_id,
        manifest_artifact_id=revision.manifest_artifact_id,
        manifest_checksum=revision.manifest_checksum,
    )

    # Not registered yet.
    await _fails(
        db_session,
        IntegrityError,
        "fk_dataset_version_members_scene_revision",
        lambda: members.add(dataset_id=dataset_id, version="v1", members=[pin]),
    )

    await PostgresSceneRepository(db_session).register_revision(revision)
    await members.add(dataset_id=dataset_id, version="v1", members=[pin])

    async def wrong_domain():
        await db_session.execute(
            text(
                "INSERT INTO dataset_version_members (dataset_version_id, unit_id, "
                "domain, scene_id, episode_id, manifest_artifact_id, "
                "manifest_checksum) VALUES (:v, 'episode-x', 'episode', :s, NULL, "
                ":a, :c)"
            ),
            {
                "v": f"{dataset_id}:v1",
                "s": revision.scene_id,
                "a": revision.manifest_artifact_id,
                "c": revision.manifest_checksum,
            },
        )

    await _fails(
        db_session, IntegrityError, "ck_dataset_version_members_revision", wrong_domain
    )


@pytest.mark.asyncio
async def test_membership_summary_and_scope_filter(
    db_session, unique_id, seed_dataset_version, scene_revision_for, pin_members
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    repo = PostgresSceneRepository(db_session)
    empty = await repo.summarize_membership(dataset_id=dataset_id, dataset_version="v1")
    assert (empty.scene_count, empty.observed_channels) == (0, [])

    run_a, run_b = unique_id("run"), unique_id("run")
    a = await scene_revision_for(
        db_session, source=recording_source(robot_run_id=run_a, unit_key="a")
    )
    b = await scene_revision_for(
        db_session,
        source=recording_source(robot_run_id=run_b, unit_key="b"),
        camera_channel="CAM_BACK",
        keyframe_timestamps_ns=(1_000,),
    )
    unpinned = await scene_revision_for(
        db_session, source=recording_source(robot_run_id=run_a, unit_key="c")
    )
    for revision in (a, b, unpinned):
        await repo.register_revision(revision)
    await pin_members(db_session, dataset_id=dataset_id, revisions=[a, b])

    summary = await repo.summarize_membership(
        dataset_id=dataset_id, dataset_version="v1"
    )
    assert summary.scene_count == 2
    assert summary.keyframe_count == a.keyframe_count + b.keyframe_count
    assert summary.observation_count == a.observation_count + b.observation_count
    assert summary.observed_channels == ["CAM_BACK", "CAM_FRONT", "LIDAR_TOP"]
    scope = await repo.list_member_revisions(
        dataset_id=dataset_id, dataset_version="v1", robot_run_id=run_a
    )
    assert [s.scene_id for s in scope] == [a.scene_id]

    members = PostgresDatasetVersionMemberRepository(db_session)
    assert (
        await members.remove(dataset_id=dataset_id, version="v1", unit_ids=[a.scene_id])
        == 1
    )
    assert [
        m.unit_id for m in await members.list(dataset_id=dataset_id, version="v1")
    ] == [b.scene_id]
    # Unpinning removes a member, never the revision.
    assert (
        await repo.get_revision(
            scene_id=a.scene_id, manifest_checksum=a.manifest_checksum
        )
        is not None
    )


# --- concurrency (independent committed sessions) -------------------------------


async def _committed_revision(make, **kwargs):
    async with get_async_sessionmaker()() as session:
        revision = await make(session, **kwargs)
        await session.commit()
    return revision


async def _race(first, second):
    """Run two registrations in their own transactions so that the second
    reaches its insert while the first holds its uncommitted row, then let
    the first commit. Returns (first outcome, second outcome, whether the
    second finished before the first committed)."""
    sessionmaker = get_async_sessionmaker()
    first_wrote = asyncio.Event()
    first_committed = asyncio.Event()
    finished_early = False

    async def run_first():
        async with sessionmaker() as session:
            result = await PostgresSceneRepository(session).register_revision(first)
            first_wrote.set()
            await asyncio.sleep(0.5)  # the second blocks on our key meanwhile
            first_committed.set()
            await session.commit()
            return result

    async def run_second():
        nonlocal finished_early
        await first_wrote.wait()
        async with sessionmaker() as session:
            try:
                result = await PostgresSceneRepository(session).register_revision(
                    second
                )
                await session.commit()
                return result
            except Exception as exc:  # noqa: BLE001 - reported to the test
                await session.rollback()
                return exc
            finally:
                finished_early = not first_committed.is_set()

    one, two = await asyncio.gather(run_first(), run_second())
    return one, two, finished_early


@pytest.mark.asyncio
async def test_concurrent_identical_registrations_converge_on_one_row(
    unique_id, scene_revision_for
):
    revision = await _committed_revision(
        scene_revision_for, source=recording_source(robot_run_id=unique_id("run"))
    )

    one, two, finished_early = await _race(revision, revision)

    assert not finished_early  # the second waited on the first's unique key
    assert one.decision is RevisionDecision.NEW_UNIT
    assert two.decision is RevisionDecision.EXISTING_REVISION
    async with get_async_sessionmaker()() as session:
        repo = PostgresSceneRepository(session)
        assert len(await repo.list_revisions(revision.scene_id)) == 1
        assert (await repo.get(revision.scene_id)) is not None


@pytest.mark.asyncio
async def test_concurrent_non_deterministic_registrations_one_wins_one_fails(
    unique_id, scene_revision_for
):
    source = recording_source(robot_run_id=unique_id("run"))
    one_revision = await _committed_revision(scene_revision_for, source=source)
    drifted = await _committed_revision(
        scene_revision_for, source=source, keyframe_timestamps_ns=(1_000,)
    )

    one, two, finished_early = await _race(one_revision, drifted)

    assert not finished_early
    assert one.decision is RevisionDecision.NEW_UNIT
    assert isinstance(two, NonDeterministicProducerError)
    async with get_async_sessionmaker()() as session:
        revisions = await PostgresSceneRepository(session).list_revisions(
            one_revision.scene_id
        )
    assert [r.manifest_checksum for r in revisions] == [one_revision.manifest_checksum]


@pytest.mark.asyncio
async def test_concurrent_new_revisions_of_one_unit_both_commit(
    unique_id, scene_revision_for
):
    source = recording_source(robot_run_id=unique_id("run"))
    one_revision = await _committed_revision(scene_revision_for, source=source)
    other = await _committed_revision(
        scene_revision_for, source=source, build_config={"channels": ["CAM_FRONT"]}
    )

    one, two, _ = await _race(one_revision, other)

    assert one.decision is RevisionDecision.NEW_UNIT
    assert two.decision is RevisionDecision.NEW_REVISION
    async with get_async_sessionmaker()() as session:
        repo = PostgresSceneRepository(session)
        assert len(await repo.list_revisions(one_revision.scene_id)) == 2
        assert len(await repo.list(robot_run_id=source.robot_run_id)) == 1
