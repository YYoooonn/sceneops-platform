"""Isolation tests for PostgresDatasetVersionRepository's Scene writers
(replace_scene_membership_summary / update_scene_inputs) and
update_episode_summary.

Uses a real DatasetVersionModel instance (constructible without a DB
connection) with a mocked AsyncSession whose execute()/scalar_one_or_none()
returns that same instance, so apply_values() runs for real against a real
SQLAlchemy model — this exercises the actual partial-update logic
(values_without_none + disjoint scene/episode column sets), not a mock of it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_db.models.datasets import DatasetVersionModel
from sceneops_db.postgres.datasets import PostgresDatasetVersionRepository

_NOW = datetime.now(timezone.utc)


def _model(**overrides) -> DatasetVersionModel:
    base = dict(
        id="d:v1",
        dataset_id="d",
        version="v1",
        status="registered",
        manifest_uri="s3://bucket/manifest.json",
        scene_count=5,
        keyframe_count=10,
        observation_count=20,
        episode_count=3,
        observed_channels=["CAM_FRONT"],
        required_channels=["CAM_FRONT"],
        source_dataset_id=None,
        source_dataset_version=None,
        raw_source_root_uri=None,
        created_at=_NOW,
        updated_at=_NOW,
        metadata_={},
    )
    base.update(overrides)
    return DatasetVersionModel(**base)


def _repo_with_model(model: DatasetVersionModel) -> PostgresDatasetVersionRepository:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=model)
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    return PostgresDatasetVersionRepository(session)


class TestSceneWriterIsolation:
    @pytest.mark.asyncio
    async def test_membership_summary_replacement_preserves_other_columns(self) -> None:
        model = _model(scene_count=5, episode_count=3, raw_source_root_uri="/raw")
        repo = _repo_with_model(model)

        await repo.replace_scene_membership_summary(
            dataset_id="d",
            version="v1",
            scene_count=99,
            keyframe_count=7,
            observation_count=70,
            observed_channels=["LIDAR_TOP"],
        )

        assert (model.scene_count, model.keyframe_count, model.observation_count) == (
            99,
            7,
            70,
        )
        assert model.observed_channels == ["LIDAR_TOP"]
        assert model.episode_count == 3
        assert model.raw_source_root_uri == "/raw"
        assert model.required_channels == ["CAM_FRONT"]

    @pytest.mark.asyncio
    async def test_scene_inputs_update_never_touches_membership(self) -> None:
        model = _model(manifest_uri="s3://x/m.json")
        repo = _repo_with_model(model)

        await repo.update_scene_inputs(
            dataset_id="d", version="v1", raw_source_root_uri="/raw"
        )

        assert model.raw_source_root_uri == "/raw"
        assert model.manifest_uri == "s3://x/m.json"  # omitted -> untouched
        assert (model.scene_count, model.observed_channels) == (5, ["CAM_FRONT"])


class TestEpisodeWriterIsolation:
    @pytest.mark.asyncio
    async def test_updating_episode_summary_preserves_scene_fields(self) -> None:
        model = _model(scene_count=5, observed_channels=["CAM_FRONT"], episode_count=3)
        repo = _repo_with_model(model)

        await repo.update_episode_summary(
            dataset_id="d", version="v1", episode_count=99
        )

        assert model.episode_count == 99
        assert model.scene_count == 5  # untouched by an Episode-only update
        assert model.observed_channels == ["CAM_FRONT"]  # untouched
