"""Integration coverage for DatasetVersion against real Postgres."""

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
