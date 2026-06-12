"""Tests for raw source root URI resolution in BuildScenesJobHandler.

Covers:
- _require_version_with_source: missing version raises ValueError
- _require_version_with_source: missing raw_source_root_uri raises ValueError
- _require_version_with_source: valid version returns record
- execution.dataset_version_record.raw_source_root_uri carries the resolved URI
- _build_adapter_factory reads raw_source_root_uri from dataset_version_record
- _build_adapter_factory passes context.raw_source_store to the adapter
- _mark_dataset_version_ingesting: no auto-create; uses version from execution
- raw_source_root_uri preserved on DatasetVersionRecord after status update
- no hardcoded /data/raw/nuscenes in handler source
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sceneops_core.datasets.schemas.enums import DatasetVersionStatus
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import BuildScenesJobParams
from sceneops_worker.jobs.dataset.build_scenes import (
    BuildScenesExecution,
    BuildScenesJobHandler,
)
from sceneops_worker.observations.artifacts import ObservationArtifactStore


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_handler() -> BuildScenesJobHandler:
    return BuildScenesJobHandler()


def _make_version(
    *,
    dataset_id: str = "nuscenes",
    version: str = "v1.0-mini",
    raw_source_root_uri: str | None = "/data/raw/nuscenes",
    status: str = "registered",
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_id=dataset_id,
        version=version,
        raw_source_root_uri=raw_source_root_uri,
        status=status,
    )


def _make_context(*, version: DatasetVersionRecord | None) -> MagicMock:
    ctx = MagicMock()
    ctx.default_dataset_id = "nuscenes"
    ctx.default_dataset_version = "v1.0-mini"
    ctx.dataset_store = AsyncMock()
    ctx.dataset_store.get_version = AsyncMock(return_value=version)
    ctx.dataset_store.save_version = AsyncMock(side_effect=lambda v: v)
    return ctx


def _make_execution(
    *,
    raw_source_root_uri: str = "/data/raw/nuscenes",
    context: MagicMock | None = None,
    params: BuildScenesJobParams | None = None,
) -> BuildScenesExecution:
    version_record = _make_version(raw_source_root_uri=raw_source_root_uri)
    return BuildScenesExecution(
        job=MagicMock(),
        params=params or BuildScenesJobParams(),
        context=context or MagicMock(),
        raw_log_id="nuscenes-v1.0-mini",
        obs_store=MagicMock(spec=ObservationArtifactStore),
        dataset_version_record=version_record,
        version_root_uri="s3://root/",
    )


# ── _require_version_with_source ──────────────────────────────────────────────


class TestRequireVersionWithSource:
    @pytest.mark.asyncio
    async def test_raises_if_version_not_registered(self) -> None:
        ctx = _make_context(version=None)
        with pytest.raises(ValueError, match="Dataset version not registered"):
            await BuildScenesJobHandler._require_version_with_source(
                ctx, "nuscenes", "v1.0-mini"
            )

    @pytest.mark.asyncio
    async def test_raises_if_raw_source_root_uri_missing(self) -> None:
        version = _make_version(raw_source_root_uri=None)
        ctx = _make_context(version=version)
        with pytest.raises(ValueError, match="no raw source root URI"):
            await BuildScenesJobHandler._require_version_with_source(
                ctx, "nuscenes", "v1.0-mini"
            )

    @pytest.mark.asyncio
    async def test_raises_if_raw_source_root_uri_empty_string(self) -> None:
        version = _make_version(raw_source_root_uri="")
        ctx = _make_context(version=version)
        with pytest.raises(ValueError, match="no raw source root URI"):
            await BuildScenesJobHandler._require_version_with_source(
                ctx, "nuscenes", "v1.0-mini"
            )

    @pytest.mark.asyncio
    async def test_returns_version_when_valid(self) -> None:
        version = _make_version(raw_source_root_uri="/data/raw/nuscenes")
        ctx = _make_context(version=version)
        result = await BuildScenesJobHandler._require_version_with_source(
            ctx, "nuscenes", "v1.0-mini"
        )
        assert result is version

    @pytest.mark.asyncio
    async def test_error_message_includes_dataset_and_version(self) -> None:
        ctx = _make_context(version=None)
        with pytest.raises(ValueError, match="nuscenes/v1.0-mini"):
            await BuildScenesJobHandler._require_version_with_source(
                ctx, "nuscenes", "v1.0-mini"
            )


# ── raw_source_root_uri lives on dataset_version_record ──────────────────────


class TestExecutionVersionRecordSourceUri:
    def test_raw_source_root_uri_on_version_record(self) -> None:
        execution = _make_execution(raw_source_root_uri="/data/raw/nuscenes")
        assert (
            execution.dataset_version_record.raw_source_root_uri == "/data/raw/nuscenes"
        )

    def test_version_record_carries_dataset_id(self) -> None:
        execution = _make_execution()
        assert execution.dataset_version_record.dataset_id == "nuscenes"

    def test_s3_raw_source_root_uri_on_version_record(self) -> None:
        execution = _make_execution(raw_source_root_uri="s3://sceneops/raw/nuscenes")
        assert (
            execution.dataset_version_record.raw_source_root_uri
            == "s3://sceneops/raw/nuscenes"
        )


# ── _build_adapter_factory uses execution URI, no fallback ───────────────────


class TestAdapterFactoryUsesExecutionUri:
    def test_adapter_receives_execution_raw_source_root_uri(self) -> None:
        handler = _make_handler()
        execution = _make_execution(raw_source_root_uri="/custom/raw/path")

        # NuScenesRawLogMocker is imported lazily inside _build_adapter_factory;
        # patch at its definition module.
        with patch(
            "sceneops_worker.datasets.ingestion.nuscenes_raw_log.NuScenesRawLogMocker"
        ) as MockMocker:
            MockMocker.return_value = MagicMock()
            handler._build_adapter_factory(
                execution=execution,
                obs_store=execution.obs_store,
            )

        call_kwargs = MockMocker.call_args.kwargs
        assert call_kwargs["source_root_uri"] == "/custom/raw/path"

    def test_adapter_receives_context_raw_source_store(self) -> None:
        handler = _make_handler()
        mock_raw_source_store = MagicMock()
        ctx = MagicMock()
        ctx.raw_source_store = mock_raw_source_store
        execution = _make_execution(context=ctx, raw_source_root_uri="/any/path")

        with patch(
            "sceneops_worker.datasets.ingestion.nuscenes_raw_log.NuScenesRawLogMocker"
        ) as MockMocker:
            MockMocker.return_value = MagicMock()
            handler._build_adapter_factory(
                execution=execution,
                obs_store=execution.obs_store,
            )

        call_kwargs = MockMocker.call_args.kwargs
        assert call_kwargs["source_store"] is mock_raw_source_store

    def test_adapter_does_not_use_hardcoded_fallback(self) -> None:
        """Verify no hardcoded /data/raw/nuscenes path reaches the adapter."""
        handler = _make_handler()
        execution = _make_execution(raw_source_root_uri="/override/path")

        with patch(
            "sceneops_worker.datasets.ingestion.nuscenes_raw_log.NuScenesRawLogMocker"
        ) as MockMocker:
            MockMocker.return_value = MagicMock()
            handler._build_adapter_factory(
                execution=execution,
                obs_store=execution.obs_store,
            )

        call_kwargs = MockMocker.call_args.kwargs
        assert call_kwargs["source_root_uri"] != "/data/raw/nuscenes"
        assert call_kwargs["source_root_uri"] == "/override/path"


# ── _mark_dataset_version_ingesting: no auto-create ──────────────────────────


class TestMarkDatasetVersionIngesting:
    @pytest.mark.asyncio
    async def test_updates_status_to_ingesting(self) -> None:
        handler = _make_handler()
        version_record = _make_version(status="registered")
        ctx = MagicMock()
        ctx.dataset_store = AsyncMock()
        ctx.dataset_store.save_version = AsyncMock(side_effect=lambda v: v)
        execution = _make_execution(context=ctx)
        execution = replace(execution, dataset_version_record=version_record)

        result = await handler._mark_dataset_version_ingesting(execution)

        ctx.dataset_store.save_version.assert_called_once()
        assert result.status == DatasetVersionStatus.INGESTING

    @pytest.mark.asyncio
    async def test_does_not_call_create_version(self) -> None:
        handler = _make_handler()
        ctx = MagicMock()
        ctx.dataset_store = AsyncMock()
        ctx.dataset_store.save_version = AsyncMock(side_effect=lambda v: v)
        ctx.dataset_store.create_version = AsyncMock()
        execution = _make_execution(context=ctx)

        await handler._mark_dataset_version_ingesting(execution)

        ctx.dataset_store.create_version.assert_not_called()

    @pytest.mark.asyncio
    async def test_preserves_raw_source_root_uri_through_status_update(self) -> None:
        handler = _make_handler()
        version_record = _make_version(
            raw_source_root_uri="/data/raw/nuscenes", status="registered"
        )
        ctx = MagicMock()
        ctx.dataset_store = AsyncMock()
        ctx.dataset_store.save_version = AsyncMock(side_effect=lambda v: v)
        execution = _make_execution(context=ctx)
        execution = replace(execution, dataset_version_record=version_record)

        result = await handler._mark_dataset_version_ingesting(execution)

        assert result.raw_source_root_uri == "/data/raw/nuscenes"
        assert result.status == DatasetVersionStatus.INGESTING


# ── no hardcoded path in handler module ──────────────────────────────────────


class TestNoHardcodedFallback:
    def test_no_hardcoded_data_raw_nuscenes_in_handler(self) -> None:
        import inspect
        import sceneops_worker.jobs.dataset.build_scenes as module

        source = inspect.getsource(module)
        assert "/data/raw/nuscenes" not in source, (
            "Hardcoded '/data/raw/nuscenes' found in build_scenes handler. "
            "Raw source root must come from DatasetVersionRecord."
        )

    def test_no_records_uri_as_source_fallback(self) -> None:
        """records_uri must not be used as a raw source root fallback."""
        import inspect
        import sceneops_worker.jobs.dataset.build_scenes as module

        source = inspect.getsource(module)
        # Should not see the old fallback pattern
        assert (
            "params.records_uri" not in source or "source_root" not in source
        ), "records_uri is being used as a source root fallback in build_scenes handler."
