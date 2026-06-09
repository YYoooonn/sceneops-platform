"""Unit tests for BuildScenesJobHandler private methods.

Covers:
- _build_result: all Phase 2 grouping-report fields flow into BuildScenesJobResult
- _resolve_raw_log_inputs: branch selection (load path vs adapter path)
- _mark_dataset_version_ingested: correct counts forwarded to dataset store
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import BuildScenesJobParams, BuildScenesJobResult
from sceneops_core.observations.schemas import (
    RawLogFrameIndex,
    RawLogManifest,
    RawLogSourceFormat,
    RawLogSourceType,
)
from sceneops_worker.jobs.dataset.build_scenes import (
    BuildScenesExecution,
    BuildScenesJobHandler,
    BuildScenesRawInputs,
)
from sceneops_worker.scenes.raw_scene_builder import SceneBuildResult
from sceneops_worker.scenes.sample_grouping import SampleGroupingReport


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_handler() -> BuildScenesJobHandler:
    return BuildScenesJobHandler()


def _make_params(
    *,
    raw_log_manifest_uri: str | None = None,
    raw_log_frame_index_uri: str | None = None,
) -> BuildScenesJobParams:
    return BuildScenesJobParams(
        raw_log_manifest_uri=raw_log_manifest_uri,
        raw_log_frame_index_uri=raw_log_frame_index_uri,
    )


def _make_version_record(
    *,
    dataset_id: str = "ds-001",
    version: str = "v1",
    raw_source_root_uri: str = "/data/raw/nuscenes",
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_id=dataset_id,
        version=version,
        raw_source_root_uri=raw_source_root_uri,
    )


def _make_execution(
    *,
    params: BuildScenesJobParams | None = None,
    dataset_id: str = "ds-001",
    dataset_version: str = "v1",
    raw_log_id: str = "ds-001-v1",
    raw_source_root_uri: str = "/data/raw/nuscenes",
) -> BuildScenesExecution:
    version_record = _make_version_record(
        dataset_id=dataset_id,
        version=dataset_version,
        raw_source_root_uri=raw_source_root_uri,
    )
    return BuildScenesExecution(
        job=MagicMock(),
        params=params or _make_params(),
        context=MagicMock(),
        raw_log_id=raw_log_id,
        obs_store=MagicMock(),
        dataset_version_record=version_record,
        version_root_uri="s3://root/ds-001/v1",
    )


def _make_raw_manifest(channels: list[str] | None = None) -> RawLogManifest:
    return RawLogManifest(
        raw_log_id="log-001",
        dataset_id="ds-001",
        dataset_version="v1",
        dataset_type="nuscenes",
        source_format=RawLogSourceFormat.NUSCENES,
        root_uri="s3://raw/",
        channels=channels or ["CAM_FRONT", "LIDAR_TOP"],
        source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
    )


def _make_raw_inputs(channels: list[str] | None = None) -> BuildScenesRawInputs:
    manifest = _make_raw_manifest(channels=channels)
    frame_index = RawLogFrameIndex(
        raw_log_id="log-001",
        dataset_id="ds-001",
        dataset_version="v1",
    )
    return BuildScenesRawInputs(
        raw_manifest=manifest,
        frame_index=frame_index,
        raw_manifest_uri="s3://raw/manifest.json",
        raw_frame_index_uri="s3://raw/frame_index.json",
    )


def _make_scene_build_result(
    *,
    scene_ids: list[str] | None = None,
    total_samples: int = 10,
    total_frames: int = 60,
    observation_count: int = 60,
    grouping_report: SampleGroupingReport | None = None,
) -> SceneBuildResult:
    return SceneBuildResult(
        scene_ids=scene_ids or ["sc-001"],
        scene_manifest_uris=["s3://scenes/sc-001/manifest.json"],
        segment_index_uri="s3://segments/index.json",
        total_samples=total_samples,
        total_frames=total_frames,
        observation_count=observation_count,
        grouping_report=grouping_report
        or SampleGroupingReport(
            total_samples_built=total_samples,
            sample_count_before_filtering=total_samples,
            sample_count_after_filtering=total_samples,
        ),
    )


# ── _build_result: Phase 2 grouping report fields ─────────────────────────────


class TestBuildResult:
    def _run(
        self,
        *,
        grouping_report: SampleGroupingReport | None = None,
        channels: list[str] | None = None,
    ) -> BuildScenesJobResult:
        handler = _make_handler()
        execution = _make_execution()
        raw_inputs = _make_raw_inputs(channels=channels)
        scene_result = _make_scene_build_result(grouping_report=grouping_report)
        return handler._build_result(
            execution=execution,
            raw_inputs=raw_inputs,
            scene_build_result=scene_result,
        )

    def test_phase2_report_fields_zero_when_no_policy(self) -> None:
        result = self._run()
        assert result.sample_count_before_filtering == 10
        assert result.sample_count_after_filtering == 10
        assert result.dropped_sample_count == 0
        assert result.warned_sample_count == 0
        assert result.samples_with_missing_channels_count == 0
        assert result.missing_channel_counts_by_channel == {}

    def test_phase2_report_fields_populated_from_grouping_report(self) -> None:
        report = SampleGroupingReport(
            total_samples_built=8,
            sample_count_before_filtering=10,
            sample_count_after_filtering=8,
            dropped_sample_count=2,
            warned_sample_count=3,
            samples_with_missing_channels_count=4,
            missing_channel_counts_by_channel={"CAM_FRONT": 2, "LIDAR_TOP": 1},
        )
        result = self._run(grouping_report=report)
        assert result.sample_count_before_filtering == 10
        assert result.sample_count_after_filtering == 8
        assert result.dropped_sample_count == 2
        assert result.warned_sample_count == 3
        assert result.samples_with_missing_channels_count == 4
        assert result.missing_channel_counts_by_channel == {
            "CAM_FRONT": 2,
            "LIDAR_TOP": 1,
        }

    def test_channels_sorted_from_raw_manifest(self) -> None:
        result = self._run(channels=["LIDAR_TOP", "CAM_BACK", "CAM_FRONT"])
        assert result.channels == ["CAM_BACK", "CAM_FRONT", "LIDAR_TOP"]

    def test_raw_log_id_from_execution(self) -> None:
        handler = _make_handler()
        execution = _make_execution(raw_log_id="custom-raw-log-id")
        raw_inputs = _make_raw_inputs()
        result = handler._build_result(
            execution=execution,
            raw_inputs=raw_inputs,
            scene_build_result=_make_scene_build_result(),
        )
        assert result.raw_log_id == "custom-raw-log-id"

    def test_source_type_none_when_manifest_source_type_absent(self) -> None:
        handler = _make_handler()
        params = BuildScenesJobParams()
        execution = _make_execution(params=params)

        manifest = RawLogManifest(
            raw_log_id="log-001",
            dataset_id="ds-001",
            dataset_version="v1",
            dataset_type="nuscenes",
            source_format=RawLogSourceFormat.NUSCENES,
            root_uri="s3://raw/",
            source_type=None,
        )
        raw_inputs = BuildScenesRawInputs(
            raw_manifest=manifest,
            frame_index=RawLogFrameIndex(
                raw_log_id="log-001",
                dataset_id="ds-001",
                dataset_version="v1",
            ),
            raw_manifest_uri="s3://raw/manifest.json",
            raw_frame_index_uri="s3://raw/frame_index.json",
        )
        result = handler._build_result(
            execution=execution,
            raw_inputs=raw_inputs,
            scene_build_result=_make_scene_build_result(),
        )
        assert result.source_type is None


# ── _resolve_raw_log_inputs: branch selection ──────────────────────────────────


class TestResolveRawLogInputsBranching:
    @pytest.mark.asyncio
    async def test_uses_load_path_when_both_uris_provided(self) -> None:
        handler = _make_handler()
        params = _make_params(
            raw_log_manifest_uri="s3://raw/manifest.json",
            raw_log_frame_index_uri="s3://raw/frame_index.json",
        )
        execution = _make_execution(params=params)

        expected_inputs = _make_raw_inputs()

        with (
            patch.object(
                BuildScenesJobHandler,
                "_load_raw_artifacts",
                new=AsyncMock(
                    return_value=(
                        expected_inputs.raw_manifest,
                        expected_inputs.frame_index,
                    )
                ),
            ) as mock_load,
            patch.object(
                handler,
                "_build_raw_log_with_adapter",
                new=AsyncMock(),
            ) as mock_adapter,
        ):
            result = await handler._resolve_raw_log_inputs(execution)

        mock_load.assert_called_once()
        mock_adapter.assert_not_called()
        assert result.raw_manifest_uri == "s3://raw/manifest.json"
        assert result.raw_frame_index_uri == "s3://raw/frame_index.json"

    @pytest.mark.asyncio
    async def test_uses_adapter_path_when_manifest_uri_missing(self) -> None:
        handler = _make_handler()
        params = _make_params(
            raw_log_manifest_uri=None,
            raw_log_frame_index_uri="s3://raw/frame_index.json",
        )
        execution = _make_execution(params=params)
        expected_inputs = _make_raw_inputs()

        with (
            patch.object(
                BuildScenesJobHandler,
                "_load_raw_artifacts",
                new=AsyncMock(),
            ) as mock_load,
            patch.object(
                handler,
                "_build_raw_log_with_adapter",
                new=AsyncMock(return_value=expected_inputs),
            ) as mock_adapter,
        ):
            result = await handler._resolve_raw_log_inputs(execution)

        mock_load.assert_not_called()
        mock_adapter.assert_called_once()
        assert result is expected_inputs

    @pytest.mark.asyncio
    async def test_uses_adapter_path_when_frame_index_uri_missing(self) -> None:
        handler = _make_handler()
        params = _make_params(
            raw_log_manifest_uri="s3://raw/manifest.json",
            raw_log_frame_index_uri=None,
        )
        execution = _make_execution(params=params)
        expected_inputs = _make_raw_inputs()

        with (
            patch.object(
                BuildScenesJobHandler,
                "_load_raw_artifacts",
                new=AsyncMock(),
            ) as mock_load,
            patch.object(
                handler,
                "_build_raw_log_with_adapter",
                new=AsyncMock(return_value=expected_inputs),
            ) as mock_adapter,
        ):
            await handler._resolve_raw_log_inputs(execution)

        mock_load.assert_not_called()
        mock_adapter.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_adapter_path_when_both_uris_missing(self) -> None:
        handler = _make_handler()
        params = _make_params()
        execution = _make_execution(params=params)
        expected_inputs = _make_raw_inputs()

        with (
            patch.object(
                BuildScenesJobHandler,
                "_load_raw_artifacts",
                new=AsyncMock(),
            ) as mock_load,
            patch.object(
                handler,
                "_build_raw_log_with_adapter",
                new=AsyncMock(return_value=expected_inputs),
            ) as mock_adapter,
        ):
            await handler._resolve_raw_log_inputs(execution)

        mock_load.assert_not_called()
        mock_adapter.assert_called_once()


# ── _mark_dataset_version_ingested: count forwarding ─────────────────────────


class TestMarkDatasetVersionIngested:
    @pytest.mark.asyncio
    async def test_forwards_scene_and_sample_counts_to_store(self) -> None:
        handler = _make_handler()
        mock_store = AsyncMock()
        mock_store.save_version = AsyncMock(
            side_effect=lambda v: v  # return the version passed in
        )

        context = MagicMock()
        context.dataset_store = mock_store

        execution = replace(_make_execution(params=_make_params()), context=context)

        version = DatasetVersionRecord(
            dataset_id="ds-001",
            version="v1",
            status="ingesting",
            raw_source_root_uri="/data/raw/nuscenes",
        )
        raw_inputs = _make_raw_inputs(channels=["CAM_FRONT", "LIDAR_TOP"])
        scene_result = _make_scene_build_result(
            scene_ids=["sc-001", "sc-002"],
            total_samples=15,
            total_frames=90,
        )

        await handler._mark_dataset_version_ingested(
            execution=execution,
            version=version,
            raw_inputs=raw_inputs,
            scene_build_result=scene_result,
        )

        mock_store.save_version.assert_called_once()
        saved = mock_store.save_version.call_args[0][0]
        assert saved.scene_count == 2
        assert saved.sample_count == 15
        assert saved.frame_count == 90
        assert saved.channels == ["CAM_FRONT", "LIDAR_TOP"]

    @pytest.mark.asyncio
    async def test_channels_are_sorted(self) -> None:
        handler = _make_handler()
        mock_store = AsyncMock()
        mock_store.save_version = AsyncMock(side_effect=lambda v: v)

        context = MagicMock()
        context.dataset_store = mock_store

        execution = replace(_make_execution(params=_make_params()), context=context)

        version = DatasetVersionRecord(
            dataset_id="ds-001",
            version="v1",
            status="ingesting",
            raw_source_root_uri="/data/raw/nuscenes",
        )
        # deliberately out of order
        raw_inputs = _make_raw_inputs(channels=["LIDAR_TOP", "CAM_BACK", "CAM_FRONT"])
        scene_result = _make_scene_build_result()

        await handler._mark_dataset_version_ingested(
            execution=execution,
            version=version,
            raw_inputs=raw_inputs,
            scene_build_result=scene_result,
        )

        saved = mock_store.save_version.call_args[0][0]
        assert saved.channels == ["CAM_BACK", "CAM_FRONT", "LIDAR_TOP"]
