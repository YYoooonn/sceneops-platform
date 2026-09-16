"""Isolation tests for PostgresDatasetVersionRepository.update_scene_summary /
update_episode_summary (SceneOps V2 Request 03).

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
        sample_count=10,
        frame_count=20,
        episode_count=3,
        channels=["CAM_FRONT"],
        required_channels=["CAM_FRONT"],
        source_dataset_id=None,
        source_dataset_version=None,
        raw_source_root_uri=None,
        latest_validation_run_id=None,
        validation_status=None,
        should_block_pipeline=None,
        validation_report_uri=None,
        latest_profile_run_id=None,
        profile_report_uri=None,
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
    async def test_updating_scene_summary_preserves_episode_count(self) -> None:
        model = _model(scene_count=5, episode_count=3)
        repo = _repo_with_model(model)

        await repo.update_scene_summary(
            dataset_id="d",
            version="v1",
            scene_count=99,
            channels=["LIDAR_TOP"],
        )

        assert model.scene_count == 99
        assert model.channels == ["LIDAR_TOP"]
        assert model.episode_count == 3  # untouched by a Scene-only update

    @pytest.mark.asyncio
    async def test_omitted_scene_fields_are_left_untouched(self) -> None:
        """None (i.e. not passed) must not reset should_block_pipeline/etc —
        only fields actually provided change."""
        model = _model(should_block_pipeline=True, profile_report_uri="s3://x/p.json")
        repo = _repo_with_model(model)

        await repo.update_scene_summary(
            dataset_id="d", version="v1", latest_validation_run_id="run-2"
        )

        assert model.latest_validation_run_id == "run-2"
        assert model.should_block_pipeline is True  # untouched
        assert model.profile_report_uri == "s3://x/p.json"  # untouched


class TestEpisodeWriterIsolation:
    @pytest.mark.asyncio
    async def test_updating_episode_summary_preserves_scene_fields(self) -> None:
        model = _model(scene_count=5, channels=["CAM_FRONT"], episode_count=3)
        repo = _repo_with_model(model)

        await repo.update_episode_summary(
            dataset_id="d", version="v1", episode_count=99
        )

        assert model.episode_count == 99
        assert model.scene_count == 5  # untouched by an Episode-only update
        assert model.channels == ["CAM_FRONT"]  # untouched
