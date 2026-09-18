"""Integration coverage for SceneRecord, EpisodeRecord, and ArtifactRecord
persistence against real Postgres."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.episodes.schemas.records import EpisodeRecord
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.scenes import PostgresSceneRepository


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
