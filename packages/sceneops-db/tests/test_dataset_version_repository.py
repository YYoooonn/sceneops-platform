"""Integration coverage for DatasetVersion against real Postgres.

Priority: the Scene/Episode domain-summary isolation invariant established
in packages/sceneops-db/sceneops_db/postgres/datasets.py — this can only be
verified against the real partial-update SQL, not a mock.
"""

from __future__ import annotations

import pytest

from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_db.postgres.datasets import (
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)


async def _create_dataset(db_session, dataset_id: str) -> None:
    """dataset_versions.dataset_id is FK-constrained to datasets.dataset_id."""
    await PostgresDatasetRepository(db_session).create(
        DatasetRecord(dataset_id=dataset_id)
    )


@pytest.mark.asyncio
async def test_create_and_get_round_trip(db_session, unique_id):
    repo = PostgresDatasetVersionRepository(db_session)
    dataset_id = unique_id("ds")
    version = "v1"
    await _create_dataset(db_session, dataset_id)

    created = await repo.create(
        DatasetVersionRecord(dataset_id=dataset_id, version=version)
    )
    assert created.dataset_id == dataset_id
    assert created.version == version

    fetched = await repo.get(dataset_id=dataset_id, version=version)
    assert fetched is not None
    assert fetched.dataset_id == dataset_id
    assert fetched.scene is None
    assert fetched.episode is None


@pytest.mark.asyncio
async def test_get_missing_version_returns_none(db_session, unique_id):
    repo = PostgresDatasetVersionRepository(db_session)
    result = await repo.get(dataset_id=unique_id("nonexistent"), version="v1")
    assert result is None


@pytest.mark.asyncio
async def test_scene_summary_update_does_not_overwrite_episode_summary(
    db_session, unique_id
):
    repo = PostgresDatasetVersionRepository(db_session)
    dataset_id = unique_id("ds")
    version = "v1"
    await _create_dataset(db_session, dataset_id)
    await repo.create(DatasetVersionRecord(dataset_id=dataset_id, version=version))

    await repo.update_episode_summary(
        dataset_id=dataset_id, version=version, episode_count=7
    )
    await repo.replace_scene_membership_summary(
        dataset_id=dataset_id,
        version=version,
        scene_count=3,
        keyframe_count=30,
        observation_count=90,
        observed_channels=["CAM_FRONT"],
    )

    result = await repo.get(dataset_id=dataset_id, version=version)
    assert result.scene.scene_count == 3
    assert result.scene.keyframe_count == 30
    # The Episode summary written first must be untouched by the Scene update.
    assert result.episode.episode_count == 7


@pytest.mark.asyncio
async def test_episode_summary_update_does_not_overwrite_scene_summary(
    db_session, unique_id
):
    repo = PostgresDatasetVersionRepository(db_session)
    dataset_id = unique_id("ds")
    version = "v1"
    await _create_dataset(db_session, dataset_id)
    await repo.create(DatasetVersionRecord(dataset_id=dataset_id, version=version))

    await repo.replace_scene_membership_summary(
        dataset_id=dataset_id,
        version=version,
        scene_count=5,
        keyframe_count=0,
        observation_count=5,
        observed_channels=["LIDAR_TOP"],
    )
    await repo.update_episode_summary(
        dataset_id=dataset_id, version=version, episode_count=2
    )

    result = await repo.get(dataset_id=dataset_id, version=version)
    assert result.episode.episode_count == 2
    # The Scene summary written first must be untouched by the Episode update.
    assert result.scene.scene_count == 5
    assert result.scene.observed_channels == ["LIDAR_TOP"]


@pytest.mark.asyncio
async def test_scene_inputs_partial_update_never_touches_membership_summary(
    db_session, unique_id
):
    """None kwargs mean 'leave untouched', and the non-membership Scene
    inputs can never overwrite registrar-owned counts."""
    repo = PostgresDatasetVersionRepository(db_session)
    dataset_id = unique_id("ds")
    version = "v1"
    await _create_dataset(db_session, dataset_id)
    await repo.create(DatasetVersionRecord(dataset_id=dataset_id, version=version))
    await repo.replace_scene_membership_summary(
        dataset_id=dataset_id,
        version=version,
        scene_count=10,
        keyframe_count=100,
        observation_count=400,
        observed_channels=["CAM_FRONT"],
    )

    await repo.update_scene_inputs(
        dataset_id=dataset_id, version=version, required_channels=["CAM_FRONT"]
    )
    await repo.update_scene_inputs(
        dataset_id=dataset_id, version=version, manifest_uri="s3://b/dataset.json"
    )

    result = await repo.get(dataset_id=dataset_id, version=version)
    assert result.scene.required_channels == ["CAM_FRONT"]
    assert result.scene.manifest_uri == "s3://b/dataset.json"
    assert (result.scene.scene_count, result.scene.observation_count) == (10, 400)


@pytest.mark.asyncio
async def test_list_filters_by_dataset_id(db_session, unique_id):
    repo = PostgresDatasetVersionRepository(db_session)
    dataset_id_a = unique_id("ds-a")
    dataset_id_b = unique_id("ds-b")
    await _create_dataset(db_session, dataset_id_a)
    await _create_dataset(db_session, dataset_id_b)
    await repo.create(DatasetVersionRecord(dataset_id=dataset_id_a, version="v1"))
    await repo.create(DatasetVersionRecord(dataset_id=dataset_id_b, version="v1"))

    result = await repo.list(dataset_id=dataset_id_a)
    assert [v.dataset_id for v in result] == [dataset_id_a]
