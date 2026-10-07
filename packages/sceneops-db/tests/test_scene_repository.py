"""SceneRecord persistence and the database half of its contract against
real Postgres: revision-pinned projection rows, registrar-only write
operations, membership aggregation, and the constraints that keep a row
from existing without its DatasetVersion, manifest artifact or RobotRun
(ADR-007 §13.3, §16, I-1)."""

from __future__ import annotations

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from sceneops_core.scenes.testing import recording_source
from sceneops_db.models.datasets import DatasetVersionModel
from sceneops_db.postgres.datasets import PostgresDatasetVersionRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.session import get_async_sessionmaker


@pytest.mark.asyncio
async def test_insert_get_round_trip(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await scene_record_for(db_session, dataset_id=dataset_id)

    repo = PostgresSceneRepository(db_session)
    inserted = await repo.insert(record)
    fetched = await repo.get(record.scene_id)

    assert fetched is not None
    assert fetched.model_dump(
        exclude={"registered_at", "updated_at"}
    ) == record.model_dump(exclude={"registered_at", "updated_at"})
    assert inserted.registered_at is not None
    assert fetched.observed_channels == ["CAM_FRONT", "LIDAR_TOP"]


@pytest.mark.asyncio
async def test_list_filters(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    dataset_id = unique_id("ds")
    run_id = unique_id("run")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    await seed_dataset_version(db_session, dataset_id=dataset_id, version="v2")
    repo = PostgresSceneRepository(db_session)

    other_run = await scene_record_for(db_session, dataset_id=dataset_id)
    other_version = await scene_record_for(
        db_session, dataset_id=dataset_id, dataset_version="v2"
    )
    recorded = await scene_record_for(
        db_session,
        dataset_id=dataset_id,
        source=recording_source(robot_run_id=run_id),
    )
    for record in (other_run, other_version, recorded):
        await repo.insert(record)

    in_v1 = await repo.list(dataset_id=dataset_id, dataset_version="v1")
    assert {s.scene_id for s in in_v1} == {other_run.scene_id, recorded.scene_id}
    assert [
        s.scene_id for s in await repo.list(dataset_id=dataset_id, robot_run_id=run_id)
    ] == [recorded.scene_id]
    scope = await repo.list_recording_scope(
        dataset_id=dataset_id, dataset_version="v1", robot_run_id=run_id
    )
    assert [s.scene_id for s in scope] == [recorded.scene_id]
    fetched = await repo.get(recorded.scene_id)
    assert (fetched.robot_run_id, fetched.window_clock) == (run_id, "mcap_log_time")
    assert fetched.window_end_timestamp_ns > fetched.window_start_timestamp_ns


@pytest.mark.asyncio
async def test_replace_revision_repoints_in_place(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    await seed_dataset_version(db_session, dataset_id=dataset_id, version="v2")
    repo = PostgresSceneRepository(db_session)
    source = recording_source(robot_run_id=unique_id("run"))
    first = await scene_record_for(db_session, dataset_id=dataset_id, source=source)
    await repo.insert(first)

    second = await scene_record_for(
        db_session,
        dataset_id=dataset_id,
        source=source,
        keyframe_timestamps_ns=(1_000, 2_000, 3_000),
    )
    assert second.scene_id == first.scene_id
    replaced = await repo.replace_revision(second)

    assert replaced.manifest_artifact_id == second.manifest_artifact_id
    assert replaced.manifest_checksum == second.manifest_checksum
    assert replaced.keyframe_count == 3
    assert len(await repo.list(dataset_id=dataset_id)) == 1

    with pytest.raises(ValueError, match="belongs to"):
        await repo.replace_revision(second.model_copy(update={"dataset_version": "v2"}))
    with pytest.raises(ValueError, match="not found"):
        await repo.replace_revision(
            second.model_copy(update={"scene_id": "scene-missing"})
        )


@pytest.mark.asyncio
async def test_delete_and_membership_summary(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    repo = PostgresSceneRepository(db_session)

    empty = await repo.summarize_membership(dataset_id=dataset_id, dataset_version="v1")
    assert (empty.scene_count, empty.keyframe_count, empty.observation_count) == (
        0,
        0,
        0,
    )
    assert empty.observed_channels == []

    a = await scene_record_for(
        db_session, dataset_id=dataset_id, source=recording_source(unit_key="a")
    )
    b = await scene_record_for(
        db_session,
        dataset_id=dataset_id,
        source=recording_source(unit_key="b"),
        camera_channel="CAM_BACK",
        keyframe_timestamps_ns=(1_000,),
    )
    await repo.insert(a)
    await repo.insert(b)

    summary = await repo.summarize_membership(
        dataset_id=dataset_id, dataset_version="v1"
    )
    assert summary.scene_count == 2
    assert summary.keyframe_count == a.keyframe_count + b.keyframe_count
    assert summary.observation_count == a.observation_count + b.observation_count
    assert summary.observed_channels == ["CAM_BACK", "CAM_FRONT", "LIDAR_TOP"]

    assert await repo.delete([a.scene_id]) == 1
    assert await repo.get(a.scene_id) is None
    after = await repo.summarize_membership(dataset_id=dataset_id, dataset_version="v1")
    assert (after.scene_count, after.observed_channels) == (
        1,
        ["CAM_BACK", "LIDAR_TOP"],
    )


@pytest.mark.asyncio
async def test_scene_requires_existing_dataset_version(
    db_session, unique_id, scene_record_for
):
    record = await scene_record_for(db_session, dataset_id=unique_id("ds-missing"))
    with pytest.raises(IntegrityError, match="fk_scenes_dataset_version"):
        await PostgresSceneRepository(db_session).insert(record)


@pytest.mark.asyncio
async def test_scene_requires_registered_manifest_artifact(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await scene_record_for(db_session, dataset_id=dataset_id)
    with pytest.raises(IntegrityError, match="manifest_artifact_id"):
        await PostgresSceneRepository(db_session).insert(
            record.model_copy(update={"manifest_artifact_id": "art-does-not-exist"})
        )


@pytest.mark.asyncio
async def test_recording_scene_requires_registered_robot_run(
    db_session, unique_id, seed_dataset_version, scene_record_for
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await scene_record_for(
        db_session,
        dataset_id=dataset_id,
        source=recording_source(robot_run_id=unique_id("run-unregistered")),
        seed_run=False,
    )
    with pytest.raises(IntegrityError, match="robot_run_id"):
        await PostgresSceneRepository(db_session).insert(record)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        {"robot_run_id": "run-x"},
        {"window_start_timestamp_ns": 5, "window_end_timestamp_ns": 5},
        {"window_start_timestamp_ns": 6, "window_end_timestamp_ns": 5},
    ],
)
async def test_projection_check_constraints(
    db_session, unique_id, seed_dataset_version, scene_record_for, update
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    record = await scene_record_for(db_session, dataset_id=dataset_id)
    with pytest.raises(IntegrityError, match="ck_scenes_|robot_run_id"):
        await PostgresSceneRepository(db_session).insert(
            record.model_copy(update=update)
        )


@pytest.mark.asyncio
async def test_scenes_persist_across_sessions_and_block_dataset_version_deletion(
    unique_id, seed_dataset_version, scene_record_for
):
    """Committed rows: the same recording unit in two DatasetVersions is two
    independent Scenes, and a DatasetVersion with members cannot be
    deleted out from under them."""
    sessionmaker = get_async_sessionmaker()
    dataset_id = unique_id("ds")
    source = recording_source(robot_run_id=unique_id("run"), unit_key="segment-000000")
    async with sessionmaker() as session:
        await seed_dataset_version(session, dataset_id=dataset_id, version="v1")
        await seed_dataset_version(session, dataset_id=dataset_id, version="v2")
        repo = PostgresSceneRepository(session)
        a = await scene_record_for(session, dataset_id=dataset_id, source=source)
        b = await scene_record_for(
            session, dataset_id=dataset_id, dataset_version="v2", source=source
        )
        await repo.insert(a)
        await repo.insert(b)
        await session.commit()
    assert a.scene_id != b.scene_id

    async with sessionmaker() as session:
        repo = PostgresSceneRepository(session)
        assert (await repo.get(a.scene_id)).dataset_version == "v1"
        assert (await repo.get(b.scene_id)).dataset_version == "v2"

    async with sessionmaker() as session:
        with pytest.raises(IntegrityError, match="fk_scenes_dataset_version"):
            await session.execute(
                delete(DatasetVersionModel).where(
                    DatasetVersionModel.dataset_id == dataset_id
                )
            )
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_membership_summary_replacement_is_scoped_to_scene_columns(
    db_session, unique_id, seed_dataset_version
):
    dataset_id = unique_id("ds")
    await seed_dataset_version(db_session, dataset_id=dataset_id)
    versions = PostgresDatasetVersionRepository(db_session)
    await versions.update_episode_summary(
        dataset_id=dataset_id, version="v1", episode_count=4
    )

    result = await versions.replace_scene_membership_summary(
        dataset_id=dataset_id,
        version="v1",
        scene_count=2,
        keyframe_count=5,
        observation_count=9,
        observed_channels=["CAM_FRONT"],
    )
    assert (result.scene.scene_count, result.scene.keyframe_count) == (2, 5)
    assert result.scene.observation_count == 9
    assert result.scene.observed_channels == ["CAM_FRONT"]
    assert result.episode.episode_count == 4
