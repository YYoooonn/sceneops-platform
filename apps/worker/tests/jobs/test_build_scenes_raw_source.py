"""Tests for raw source root URI resolution in BuildScenesJobHandler.

Covers:
- _require_version_with_source: missing version raises ValueError
- _require_version_with_source: missing raw_source_root_uri raises ValueError
- _require_version_with_source: valid version returns record
- execution.dataset_version_record.raw_source_root_uri carries the resolved URI
- _build_adapter_factory (RosbagAdapter/REAL_ROBOT_LOG path) reads
  raw_source_root_uri from dataset_version_record, no hardcoded fallback
- _run_nuscenes_ingest (SceneOps V2 Request 4.6): dispatches through the
  generic IntegrationExecutor, reads produced artifacts back via
  _load_raw_artifacts, requires source_format_version with no fallback
- _build_raw_log_with_adapter dispatches nuscenes_raw_log_mock to
  _run_nuscenes_ingest and everything else to the legacy adapter path
- build_scenes never calls create_version (DatasetVersion must pre-exist)
- no hardcoded /data/raw/nuscenes in handler source
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.datasets.schemas.summaries import SceneVersionSummary
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationResult,
)
from sceneops_core.jobs.schemas import BuildScenesJobParams
from sceneops_core.observations.schemas import RawLogSourceType
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
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
        status=status,
        scene=SceneVersionSummary(raw_source_root_uri=raw_source_root_uri)
        if raw_source_root_uri
        else None,
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
            execution.dataset_version_record.scene.raw_source_root_uri
            == "/data/raw/nuscenes"
        )

    def test_version_record_carries_dataset_id(self) -> None:
        execution = _make_execution()
        assert execution.dataset_version_record.dataset_id == "nuscenes"

    def test_s3_raw_source_root_uri_on_version_record(self) -> None:
        execution = _make_execution(raw_source_root_uri="s3://sceneops/raw/nuscenes")
        assert (
            execution.dataset_version_record.scene.raw_source_root_uri
            == "s3://sceneops/raw/nuscenes"
        )


# ── _build_adapter_factory uses execution URI, no fallback ───────────────────
# SceneOps V2 Request 4.6: _build_adapter_factory now only ever registers
# RosbagAdapter/REAL_ROBOT_LOG -- nuScenes dispatches through
# _run_nuscenes_ingest instead (see TestNuscenesIngestDispatch below).


class TestAdapterFactoryUsesExecutionUri:
    def test_adapter_receives_execution_raw_source_root_uri(self) -> None:
        handler = _make_handler()
        execution = _make_execution(raw_source_root_uri="/custom/raw/path")

        # RosbagAdapter is imported lazily inside _build_adapter_factory;
        # patch at its definition module.
        with patch(
            "sceneops_worker.datasets.ingestion.rosbag_raw_log.RosbagAdapter"
        ) as MockAdapter:
            MockAdapter.return_value = MagicMock()
            handler._build_adapter_factory(
                execution=execution,
                obs_store=execution.obs_store,
            )

        call_kwargs = MockAdapter.call_args.kwargs
        assert call_kwargs["source_root_uri"] == "/custom/raw/path"

    def test_adapter_receives_context_raw_source_store(self) -> None:
        handler = _make_handler()
        mock_raw_source_store = MagicMock()
        ctx = MagicMock()
        ctx.raw_source_store = mock_raw_source_store
        execution = _make_execution(context=ctx, raw_source_root_uri="/any/path")

        with patch(
            "sceneops_worker.datasets.ingestion.rosbag_raw_log.RosbagAdapter"
        ) as MockAdapter:
            MockAdapter.return_value = MagicMock()
            handler._build_adapter_factory(
                execution=execution,
                obs_store=execution.obs_store,
            )

        call_kwargs = MockAdapter.call_args.kwargs
        assert call_kwargs["source_store"] is mock_raw_source_store

    def test_adapter_does_not_use_hardcoded_fallback(self) -> None:
        """Verify no hardcoded /data/raw/nuscenes path reaches the adapter."""
        handler = _make_handler()
        execution = _make_execution(raw_source_root_uri="/override/path")

        with patch(
            "sceneops_worker.datasets.ingestion.rosbag_raw_log.RosbagAdapter"
        ) as MockAdapter:
            MockAdapter.return_value = MagicMock()
            handler._build_adapter_factory(
                execution=execution,
                obs_store=execution.obs_store,
            )

        call_kwargs = MockAdapter.call_args.kwargs
        assert call_kwargs["source_root_uri"] != "/data/raw/nuscenes"
        assert call_kwargs["source_root_uri"] == "/override/path"

    def test_factory_no_longer_registers_nuscenes_source_type(self) -> None:
        handler = _make_handler()
        execution = _make_execution(raw_source_root_uri="/any/path")

        with patch("sceneops_worker.datasets.ingestion.rosbag_raw_log.RosbagAdapter"):
            factory = handler._build_adapter_factory(
                execution=execution,
                obs_store=execution.obs_store,
            )

        with pytest.raises(ValueError, match="No RawLogAdapter registered"):
            factory.get(RawLogSourceType.NUSCENES_RAW_LOG_MOCK)


# ── _run_nuscenes_ingest / dispatch (SceneOps V2 Request 4.6) ────────────────


class TestNuscenesIngestDispatch:
    @staticmethod
    def _integration_result() -> IntegrationResult:
        return IntegrationResult(
            operation=IntegrationOperation.INGEST,
            external_ref=ExternalDatasetRef(
                format="nuscenes",
                format_version="v1.0-mini",
                uri="/data/raw/nuscenes",
            ),
            canonical_ref=CanonicalDatasetRef(
                dataset_id="nuscenes", dataset_version="v1.0-mini"
            ),
            produced_artifacts={
                "raw_log_manifest": ArtifactRef(
                    kind=ArtifactKind.RAW_LOG_MANIFEST,
                    uri="s3://sceneops/artifacts/raw/log/raw_log.json",
                ),
                "raw_log_frame_index": ArtifactRef(
                    kind=ArtifactKind.RAW_LOG_FRAME_INDEX,
                    uri="s3://sceneops/artifacts/raw/log/frames.json",
                ),
            },
        )

    @pytest.mark.asyncio
    async def test_build_raw_log_with_adapter_dispatches_nuscenes_to_ingest(
        self,
    ) -> None:
        handler = _make_handler()
        execution = _make_execution(
            params=BuildScenesJobParams(
                source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
                source_format_version="v1.0-mini",
            )
        )

        with (
            patch.object(
                handler, "_run_nuscenes_ingest", new=AsyncMock(return_value="sentinel")
            ) as mock_ingest,
            patch.object(
                handler, "_build_raw_log_with_legacy_adapter", new=AsyncMock()
            ) as mock_legacy,
        ):
            result = await handler._build_raw_log_with_adapter(execution)

        mock_ingest.assert_called_once_with(execution)
        mock_legacy.assert_not_called()
        assert result == "sentinel"

    @pytest.mark.asyncio
    async def test_build_raw_log_with_adapter_dispatches_rosbag_to_legacy(
        self,
    ) -> None:
        handler = _make_handler()
        execution = _make_execution(
            params=BuildScenesJobParams(source_type=RawLogSourceType.REAL_ROBOT_LOG)
        )

        with (
            patch.object(
                handler, "_run_nuscenes_ingest", new=AsyncMock()
            ) as mock_ingest,
            patch.object(
                handler,
                "_build_raw_log_with_legacy_adapter",
                new=AsyncMock(return_value="sentinel"),
            ) as mock_legacy,
        ):
            result = await handler._build_raw_log_with_adapter(execution)

        mock_legacy.assert_called_once_with(
            execution=execution, source_type=RawLogSourceType.REAL_ROBOT_LOG
        )
        mock_ingest.assert_not_called()
        assert result == "sentinel"

    @pytest.mark.asyncio
    async def test_missing_source_format_version_raises_no_fallback(self) -> None:
        """Defense in depth: BuildScenesJobParams' own validator already
        rejects this at construction time (like
        test_ingest_scenes.py's own bypass test) -- model_construct()
        bypasses it so _run_nuscenes_ingest's own check is what's under
        test here."""
        handler = _make_handler()
        params = BuildScenesJobParams.model_construct(
            source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
            source_format_version=None,
        )
        execution = _make_execution(params=params)

        with pytest.raises(ValueError, match="source_format_version"):
            await handler._run_nuscenes_ingest(execution)

    @pytest.mark.asyncio
    async def test_run_nuscenes_ingest_uses_executor_and_loads_produced_artifacts(
        self,
    ) -> None:
        handler = _make_handler()
        execution = _make_execution(
            raw_source_root_uri="/data/raw/nuscenes",
            params=BuildScenesJobParams(
                source_type=RawLogSourceType.NUSCENES_RAW_LOG_MOCK,
                source_format_version="v1.0-mini",
                max_source_sequences=2,
            ),
        )
        execution.obs_store.raw_log_manifest_uri = MagicMock(
            return_value="s3://sceneops/artifacts/raw/log/raw_log.json"
        )
        execution.obs_store.raw_frame_index_uri = MagicMock(
            return_value="s3://sceneops/artifacts/raw/log/frames.json"
        )

        integration_result = self._integration_result()
        mock_executor_instance = AsyncMock()
        mock_executor_instance.execute = AsyncMock(return_value=integration_result)

        with (
            patch.object(
                BuildScenesJobHandler,
                "_load_raw_artifacts",
                new=AsyncMock(return_value=("MANIFEST", "FRAME_INDEX")),
            ) as mock_load,
            patch(
                "sceneops_worker.integration_execution.HttpIntegrationExecutor",
                return_value=mock_executor_instance,
            ) as MockExecutorClass,
            patch(
                "sceneops_worker.datasets.ingestion.nuscenes_ingestion.build_nuscenes_http_config"
            ) as mock_build_config,
        ):
            mock_build_config.return_value = MagicMock()
            result = await handler._run_nuscenes_ingest(execution)

        # Request built with canonical identity + real source root, never a
        # hardcoded fallback.
        request = mock_executor_instance.execute.call_args.args[0]
        assert request.external_ref.uri == "/data/raw/nuscenes"
        assert request.external_ref.format_version == "v1.0-mini"
        assert request.config["max_source_sequences"] == 2

        MockExecutorClass.assert_called_once_with(mock_build_config.return_value)
        mock_load.assert_called_once_with(
            obs_store=execution.obs_store,
            manifest_uri="s3://sceneops/artifacts/raw/log/raw_log.json",
            frame_index_uri="s3://sceneops/artifacts/raw/log/frames.json",
        )
        assert result.raw_manifest == "MANIFEST"
        assert result.frame_index == "FRAME_INDEX"
        assert result.raw_manifest_uri == "s3://sceneops/artifacts/raw/log/raw_log.json"
        assert (
            result.raw_frame_index_uri == "s3://sceneops/artifacts/raw/log/frames.json"
        )


# ── build_scenes never auto-creates or mutates DatasetVersion.status ─────────
# SceneOps V2 Request 05: build_scenes no longer has a
# _mark_dataset_version_ingesting step at all — DatasetVersion.status is
# generic now, and _require_version_with_source already requires the version
# to pre-exist (raises otherwise), so there was never a create_version path
# to test here either.


class TestNoDatasetVersionAutoCreate:
    @pytest.mark.asyncio
    async def test_does_not_call_create_version(self) -> None:
        handler = _make_handler()
        ctx = MagicMock()
        ctx.dataset_store = AsyncMock()
        ctx.dataset_store.create_version = AsyncMock()
        execution = _make_execution(context=ctx)

        await handler._update_scene_summary_after_build(
            execution=execution,
            raw_inputs=MagicMock(raw_manifest=MagicMock(channels=[])),
            scene_build_result=MagicMock(scene_ids=[], total_samples=0, total_frames=0),
        )

        ctx.dataset_store.create_version.assert_not_called()
        ctx.dataset_store.update_scene_summary.assert_called_once()


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
