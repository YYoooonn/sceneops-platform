"""Unit tests for PipelineInputResolver._build_dataset_ref (SceneOps V2 Request 02).

Covers reading Scene-owned fields from DatasetVersionRecord.scene instead of
top-level fields, and staying valid (not raising, not assuming Scene data
exists) when a DatasetVersion has no Scene summary — e.g. an episode-only
version.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.datasets.schemas import (
    DatasetValidationStatus,
    DatasetVersionRecord,
    EpisodeVersionSummary,
    SceneVersionSummary,
)
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineType,
)
from sceneops_worker.pipelines.input_resolver import PipelineInputResolver


def _pipeline_run(dataset_id="d1", dataset_version="v1") -> PipelineRunManifest:
    return PipelineRunManifest(
        pipeline_run_id="pr-1",
        type=PipelineType.RAW_LOG_SCENE_BUILDING,
        status=PipelineRunStatus.RUNNING,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )


def _resolver_with_version(
    version: DatasetVersionRecord | None,
) -> PipelineInputResolver:
    context = MagicMock()
    context.dataset_store.get_version = AsyncMock(return_value=version)
    return PipelineInputResolver(context)


class TestBuildDatasetRefWithSceneSummary:
    @pytest.mark.asyncio
    async def test_scene_fields_come_from_nested_summary(self) -> None:
        version = DatasetVersionRecord(
            dataset_id="d1",
            version="v1",
            scene=SceneVersionSummary(
                scene_count=5,
                sample_count=10,
                frame_count=20,
                channels=["CAM_FRONT"],
                required_channels=["CAM_FRONT"],
                manifest_uri="s3://bucket/manifest.json",
                latest_validation_run_id="run-val-1",
                validation_status=DatasetValidationStatus.READY,
                should_block_pipeline=False,
                validation_report_uri="s3://bucket/val.json",
                latest_profile_run_id="run-prof-1",
                profile_report_uri="s3://bucket/prof.json",
            ),
        )
        resolver = _resolver_with_version(version)

        ref = await resolver._build_dataset_ref(_pipeline_run())

        assert ref.manifest_uri == "s3://bucket/manifest.json"
        assert ref.required_channels == ["CAM_FRONT"]
        assert ref.refs == {
            "validation_report_uri": "s3://bucket/val.json",
            "profile_report_uri": "s3://bucket/prof.json",
        }
        assert ref.summary == {
            "scene_count": 5,
            "sample_count": 10,
            "frame_count": 20,
            "channels": ["CAM_FRONT"],
            "validation_run_id": "run-val-1",
            "validation_status": str(DatasetValidationStatus.READY),
            "should_block_pipeline": False,
            "profile_run_id": "run-prof-1",
        }


class TestBuildDatasetRefEpisodeOnly:
    @pytest.mark.asyncio
    async def test_no_scene_summary_resolves_to_valid_empty_ref(self) -> None:
        version = DatasetVersionRecord(
            dataset_id="d2",
            version="v1",
            episode=EpisodeVersionSummary(episode_count=3),
        )
        assert version.scene is None

        resolver = _resolver_with_version(version)

        ref = await resolver._build_dataset_ref(_pipeline_run("d2", "v1"))

        assert ref.dataset_id == "d2"
        assert ref.dataset_version == "v1"
        assert ref.manifest_uri is None
        assert ref.required_channels == []
        assert ref.refs == {}
        assert ref.summary == {}

    @pytest.mark.asyncio
    async def test_mixed_scene_and_episode_still_resolves_scene_fields(self) -> None:
        version = DatasetVersionRecord(
            dataset_id="d3",
            version="v1",
            scene=SceneVersionSummary(scene_count=2, channels=["CAM_FRONT"]),
            episode=EpisodeVersionSummary(episode_count=1),
        )

        resolver = _resolver_with_version(version)
        ref = await resolver._build_dataset_ref(_pipeline_run("d3", "v1"))

        assert ref.summary == {"scene_count": 2, "channels": ["CAM_FRONT"]}


class TestBuildDatasetRefMissingVersion:
    @pytest.mark.asyncio
    async def test_missing_dataset_version_record_still_valid(self) -> None:
        resolver = _resolver_with_version(None)

        ref = await resolver._build_dataset_ref(_pipeline_run("d4", "v1"))

        assert ref.dataset_id == "d4"
        assert ref.dataset_version == "v1"
