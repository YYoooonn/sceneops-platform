"""Integration coverage for SceneRecord, EpisodeRecord, and ArtifactRecord
persistence against real Postgres."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.episodes.schemas.records import EpisodeRecord
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_db.models.scenes import SceneModel
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository
from sceneops_db.session import get_async_sessionmaker


# ── SceneRecord ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scene_record_create_get_round_trip(db_session, unique_id):
    repo = PostgresSceneRepository(db_session)
    scene_id = unique_id("scene")
    dataset_id = unique_id("ds")

    await repo.create(
        SceneRecord(
            scene_id=scene_id,
            dataset_id=dataset_id,
            dataset_version="v1",
            status=SceneStatus.BUILT,
            sample_count=10,
            channels=["CAM_FRONT", "LIDAR_TOP"],
        )
    )

    fetched = await repo.get(scene_id)
    assert fetched is not None
    assert fetched.dataset_id == dataset_id
    assert fetched.status == SceneStatus.BUILT
    assert fetched.sample_count == 10
    assert fetched.channels == ["CAM_FRONT", "LIDAR_TOP"]


@pytest.mark.asyncio
async def test_scene_record_list_filters_by_dataset_version(db_session, unique_id):
    repo = PostgresSceneRepository(db_session)
    dataset_id = unique_id("ds")
    scene_a = unique_id("scene-a")
    scene_b = unique_id("scene-b")

    await repo.create(
        SceneRecord(scene_id=scene_a, dataset_id=dataset_id, dataset_version="v1")
    )
    await repo.create(
        SceneRecord(scene_id=scene_b, dataset_id=dataset_id, dataset_version="v2")
    )

    result = await repo.list(dataset_id=dataset_id, dataset_version="v1")
    assert [s.scene_id for s in result] == [scene_a]


@pytest.mark.asyncio
async def test_scene_record_list_filters_by_status(db_session, unique_id):
    repo = PostgresSceneRepository(db_session)
    dataset_id = unique_id("ds")
    built = unique_id("scene-built")
    validated = unique_id("scene-validated")

    await repo.create(
        SceneRecord(
            scene_id=built,
            dataset_id=dataset_id,
            dataset_version="v1",
            status=SceneStatus.BUILT,
        )
    )
    await repo.create(
        SceneRecord(
            scene_id=validated,
            dataset_id=dataset_id,
            dataset_version="v1",
            status=SceneStatus.VALIDATED,
        )
    )

    result = await repo.list(dataset_id=dataset_id, status=SceneStatus.VALIDATED)
    assert [s.scene_id for s in result] == [validated]


@pytest.mark.asyncio
async def test_scenes_from_different_datasets_persist_independently_across_new_sessions(
    unique_id,
):
    """Regression for the Scene-persistence bug (SceneOps V2): the critical
    property is visibility from a genuinely NEW session, after each write's
    own commit -- not the shared, rolled-back `db_session` fixture every
    other test in this file uses (which never really commits, so it can't
    exercise this). Two canonical DatasetVersions independently registering
    a scene derived from the same real-world/external source scene (post-fix,
    scene_ingest.py scopes scene_id by (dataset_id, dataset_version), so
    these are legitimately distinct canonical identities that merely share
    an external provenance) must both remain durable and independently
    visible. Before the fix, an unscoped scene_id let the second dataset's
    register_scene call silently overwrite the first dataset's
    already-committed row (scene_id is `scenes`' sole primary key; the old
    scene_ingest.py set it to the bare external nuScenes scene name, and
    register_scene's scene_store.get(scene_id)/update() never included
    dataset_id/dataset_version in the lookup)."""
    sessionmaker = get_async_sessionmaker()
    dataset_a = unique_id("ds-a")
    dataset_b = unique_id("ds-b")
    # What the fixed scene_ingest.py now produces for "the same" external
    # scene under two different canonical DatasetVersions.
    scene_id_a = f"{dataset_a}-v1-scene-0061"
    scene_id_b = f"{dataset_b}-v1-scene-0061"

    try:
        async with sessionmaker() as session_a:
            await PostgresSceneRepository(session_a).create(
                SceneRecord(
                    scene_id=scene_id_a,
                    dataset_id=dataset_a,
                    dataset_version="v1",
                    status=SceneStatus.BUILT,
                    sample_count=5,
                )
            )
            await session_a.commit()

        async with sessionmaker() as session_b:
            await PostgresSceneRepository(session_b).create(
                SceneRecord(
                    scene_id=scene_id_b,
                    dataset_id=dataset_b,
                    dataset_version="v1",
                    status=SceneStatus.BUILT,
                    sample_count=7,
                )
            )
            await session_b.commit()

        # A THIRD, fresh session -- the property that actually broke: does
        # each dataset's own row survive, independently, after the other
        # dataset's commit?
        async with sessionmaker() as verify_session:
            repo = PostgresSceneRepository(verify_session)
            rows_a = await repo.list(dataset_id=dataset_a, dataset_version="v1")
            rows_b = await repo.list(dataset_id=dataset_b, dataset_version="v1")

        assert [s.scene_id for s in rows_a] == [scene_id_a]
        assert [s.scene_id for s in rows_b] == [scene_id_b]
        assert rows_a[0].sample_count == 5
        assert rows_b[0].sample_count == 7
    finally:
        # This test explicitly commits (unlike db_session's auto-rollback),
        # so it must clean up after itself.
        async with sessionmaker() as cleanup_session:
            await cleanup_session.execute(
                delete(SceneModel).where(
                    SceneModel.scene_id.in_([scene_id_a, scene_id_b])
                )
            )
            await cleanup_session.commit()


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
