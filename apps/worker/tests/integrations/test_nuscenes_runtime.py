"""Tests for the extracted nuScenes INGEST integration runtime (SceneOps V2
Request 4.4): ``sceneops_worker.integrations.nuscenes``.

Covers:
- the runtime module never imports sceneops-db/celery/nuscenes at import
  time (DB-free, Celery-free, SDK loaded lazily -- same guarantee
  packages/sceneops-core/tests/test_integration_runtime.py already proves
  for the generic contract itself);
- execute() runs given only an IntegrationRequest + ArtifactStore, no
  WorkerContext/DB session/Celery job object;
- IntegrationRequest -> produced_artifacts mapping (keys, ArtifactKind,
  checksums matching the bytes actually written);
- IntegrationOperation.INGEST contract enforcement (wrong operation/format/
  missing format_version rejected before any ArtifactStore access);
- raw_log.py's manifest/frame-index output is byte-identical to what
  NuScenesRawLogMocker (the worker-side RawLogAdapter) produces from the
  same source -- proving the extraction preserved behavior, not just moved
  code;
- the real /data/raw/nuscenes v1.0-mini fixture parses end-to-end through
  both the pure reader and the full IntegrationRequest/IntegrationResult
  runtime (skipped, not failed, if that fixture isn't present).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
)
from sceneops_storage.backends.local import LocalArtifactStore

from sceneops_worker.integrations.nuscenes import IntegrationRuntimeError, execute
from sceneops_worker.integrations.nuscenes.raw_log import read_nuscenes_raw_log

REPO_ROOT = Path(__file__).resolve().parents[4]
REAL_NUSCENES_DATAROOT = REPO_ROOT / "data" / "raw" / "nuscenes"
_HAS_REAL_FIXTURE = (REAL_NUSCENES_DATAROOT / "v1.0-mini").exists()


def _request(
    *,
    operation: IntegrationOperation = IntegrationOperation.INGEST,
    format: str = "nuscenes",
    format_version: str | None = "v1.0-mini",
    uri: str = "/data/raw/nuscenes",
    config: dict | None = None,
) -> IntegrationRequest:
    canonical_inputs = {}
    if operation is IntegrationOperation.EXPORT:
        # EXPORT requires at least one canonical_inputs entry (Request
        # 4.1A) -- irrelevant to what these tests exercise (the
        # nuscenes-runtime operation guard itself), just required for a
        # valid IntegrationRequest to construct at all.
        from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef

        canonical_inputs = {
            "learning_manifest": ArtifactRef(
                kind=ArtifactKind.LEARNING_DATA_EXPORT_MANIFEST,
                uri="s3://sceneops/artifacts/learning/export.json",
            )
        }
    return IntegrationRequest(
        operation=operation,
        external_ref=ExternalDatasetRef(
            format=format, format_version=format_version or "", uri=uri
        ),
        canonical_ref=CanonicalDatasetRef(
            dataset_id="test-e2e-raw-log", dataset_version="test-v1"
        ),
        canonical_inputs=canonical_inputs,
        config=config or {},
    )


def _mock_nusc_empty() -> MagicMock:
    mock_nusc = MagicMock()
    mock_nusc.scene = []
    return mock_nusc


# ── module import boundary ───────────────────────────────────────────────────


class TestNoDbCeleryOrEagerSdkImport:
    def test_runtime_module_imports_no_db_celery_or_nuscenes(self) -> None:
        """Checked in a fresh subprocess (never via sys.modules in the
        current test process) -- other tests in this session already import
        nuscenes-devkit, so in-process sys.modules would already be
        polluted regardless of what this module itself imports."""
        import subprocess

        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import sceneops_worker.integrations.nuscenes; "
                "loaded = set(sys.modules); "
                "assert not any(n.startswith('sqlalchemy') for n in loaded); "
                "assert not any(n.startswith('celery') for n in loaded); "
                "assert not any(n.startswith('nuscenes') for n in loaded)",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr


# ── operation/format/version guards ──────────────────────────────────────────


class TestSupportedOperationGuard:
    @pytest.mark.asyncio
    async def test_rejects_export_operation(self) -> None:
        request = _request(operation=IntegrationOperation.EXPORT)
        with pytest.raises(IntegrationRuntimeError, match="unsupported operation"):
            await execute(
                request,
                artifact_store=MagicMock(),
                raw_log_id="log-1",
                manifest_uri="mem://m.json",
                frame_index_uri="mem://f.json",
            )

    @pytest.mark.asyncio
    async def test_rejects_non_nuscenes_format(self) -> None:
        request = _request(format="lerobot", uri="s3://x")
        with pytest.raises(
            IntegrationRuntimeError, match="unsupported external_ref.format"
        ):
            await execute(
                request,
                artifact_store=MagicMock(),
                raw_log_id="log-1",
                manifest_uri="mem://m.json",
                frame_index_uri="mem://f.json",
            )

    @pytest.mark.asyncio
    async def test_rejects_missing_format_version_with_no_fallback(self) -> None:
        """No source-version fallback (Request 3.2B/3.2B.1): an empty
        external_ref.format_version must fail clearly, never silently reuse
        canonical_ref.dataset_version."""
        request = _request(format_version="")
        with patch("nuscenes.nuscenes.NuScenes") as MockNuScenes:
            with pytest.raises(IntegrationRuntimeError, match="format_version"):
                await execute(
                    request,
                    artifact_store=MagicMock(),
                    raw_log_id="log-1",
                    manifest_uri="mem://m.json",
                    frame_index_uri="mem://f.json",
                )
        MockNuScenes.assert_not_called()

    @pytest.mark.asyncio
    async def test_guards_run_before_any_artifact_store_access(self) -> None:
        request = _request(operation=IntegrationOperation.EXPORT)
        artifact_store = MagicMock()
        with pytest.raises(IntegrationRuntimeError):
            await execute(
                request,
                artifact_store=artifact_store,
                raw_log_id="log-1",
                manifest_uri="mem://m.json",
                frame_index_uri="mem://f.json",
            )
        artifact_store.write_json.assert_not_called()
        artifact_store.read_bytes.assert_not_called()


# ── execute() runs with only IntegrationRequest + ArtifactStore ────────────


class TestExecuteRunsWithoutWorkerContext:
    @pytest.mark.asyncio
    async def test_execute_needs_no_db_session_or_job_object(
        self, tmp_path: Path
    ) -> None:
        """execute()'s signature accepts no WorkerContext/DB session/Celery
        job -- only a request, an ArtifactStore, and explicit destination
        URIs/raw_log_id."""
        store = LocalArtifactStore(root_uri=str(tmp_path))
        request = _request()

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc_empty()):
            result = await execute(
                request,
                artifact_store=store,
                raw_log_id="log-001",
                manifest_uri=store.join_uri(str(tmp_path), "raw_log.json"),
                frame_index_uri=store.join_uri(str(tmp_path), "frames.json"),
            )

        assert result.operation is IntegrationOperation.INGEST
        assert result.canonical_ref.dataset_id == "test-e2e-raw-log"


# ── produced_artifacts mapping correctness ──────────────────────────────────


class TestProducedArtifactsMapping:
    @pytest.mark.asyncio
    async def test_produced_artifacts_keys_and_kinds(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest_uri = store.join_uri(str(tmp_path), "raw_log.json")
        frame_index_uri = store.join_uri(str(tmp_path), "frames.json")

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc_empty()):
            result = await execute(
                _request(),
                artifact_store=store,
                raw_log_id="log-001",
                manifest_uri=manifest_uri,
                frame_index_uri=frame_index_uri,
            )

        assert set(result.produced_artifacts) == {
            "raw_log_manifest",
            "raw_log_frame_index",
        }
        assert (
            result.produced_artifacts["raw_log_manifest"].kind
            == ArtifactKind.RAW_LOG_MANIFEST
        )
        assert (
            result.produced_artifacts["raw_log_frame_index"].kind
            == ArtifactKind.RAW_LOG_FRAME_INDEX
        )
        assert result.produced_artifacts["raw_log_manifest"].uri == manifest_uri
        assert result.produced_artifacts["raw_log_frame_index"].uri == frame_index_uri

    @pytest.mark.asyncio
    async def test_checksums_match_written_bytes(self, tmp_path: Path) -> None:
        import hashlib

        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest_uri = store.join_uri(str(tmp_path), "raw_log.json")
        frame_index_uri = store.join_uri(str(tmp_path), "frames.json")

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc_empty()):
            result = await execute(
                _request(),
                artifact_store=store,
                raw_log_id="log-001",
                manifest_uri=manifest_uri,
                frame_index_uri=frame_index_uri,
            )

        manifest_bytes = await store.read_bytes(manifest_uri)
        expected = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        assert result.produced_artifacts["raw_log_manifest"].checksum == expected

    @pytest.mark.asyncio
    async def test_result_metadata_carries_manifest_summary(
        self, tmp_path: Path
    ) -> None:
        store = LocalArtifactStore(root_uri=str(tmp_path))

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc_empty()):
            result = await execute(
                _request(config={"max_source_sequences": 2}),
                artifact_store=store,
                raw_log_id="log-001",
                manifest_uri=store.join_uri(str(tmp_path), "raw_log.json"),
                frame_index_uri=store.join_uri(str(tmp_path), "frames.json"),
            )

        assert result.result_metadata["raw_log_id"] == "log-001"
        assert result.result_metadata["frame_count"] == 0


# ── extraction preserves NuScenesRawLogMocker's output exactly ─────────────


class TestWorkerAdapterEquivalence:
    @pytest.mark.asyncio
    async def test_mocker_and_pure_reader_produce_identical_manifest(
        self, tmp_path: Path
    ) -> None:
        """NuScenesRawLogMocker (kept for BuildScenesJobHandler's
        RawLogAdapter contract) must produce byte-identical output to
        calling read_nuscenes_raw_log directly -- proving the extraction
        only moved code, it didn't change behavior."""
        from sceneops_worker.datasets.ingestion.nuscenes_raw_log import (
            NuScenesRawLogMocker,
        )
        from sceneops_worker.observations.artifacts import ObservationArtifactStore

        store = LocalArtifactStore(root_uri=str(tmp_path))
        obs_store = ObservationArtifactStore(
            artifact_store=store, dataset_root_uri=str(tmp_path)
        )
        mocker = NuScenesRawLogMocker(
            source_store=MagicMock(),
            source_root_uri="/data/raw/nuscenes",
            observation_store=obs_store,
        )

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc_empty()):
            (
                manifest,
                frame_index,
                manifest_uri,
                frame_index_uri,
            ) = await mocker.build_raw_log(
                dataset_id="d",
                dataset_version="v1",
                raw_log_id="log-A",
                version_root_uri=str(tmp_path),
                params={"source_format_version": "v1.0-mini"},
            )

        with patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc_empty()):
            direct_manifest, direct_frame_index = await read_nuscenes_raw_log(
                artifact_store=store,
                source_root_uri="/data/raw/nuscenes",
                source_format_version="v1.0-mini",
                dataset_id="d",
                dataset_version="v1",
                raw_log_id="log-A",
                manifest_uri=manifest_uri,
                frame_index_uri=frame_index_uri,
            )

        assert manifest == direct_manifest
        assert frame_index == direct_frame_index


# ── real nuScenes fixture (skipped if not present) ──────────────────────────


@pytest.mark.skipif(
    not _HAS_REAL_FIXTURE,
    reason=f"real nuScenes v1.0-mini fixture not found at {REAL_NUSCENES_DATAROOT}",
)
class TestRealNuScenesFixture:
    @pytest.mark.asyncio
    async def test_pure_reader_parses_real_mini_dataset(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(root_uri=str(tmp_path))
        manifest, frame_index = await read_nuscenes_raw_log(
            artifact_store=store,
            source_root_uri=str(REAL_NUSCENES_DATAROOT),
            source_format_version="v1.0-mini",
            dataset_id="test-e2e-raw-log",
            dataset_version="test-v1",
            raw_log_id="log-real",
            manifest_uri=store.join_uri(str(tmp_path), "raw_log.json"),
            frame_index_uri=store.join_uri(str(tmp_path), "frames.json"),
            max_source_sequences=2,
        )

        assert manifest.sequence_count == 2
        assert manifest.frame_count == len(frame_index.frames)
        assert manifest.frame_count > 0
        assert {"CAM_FRONT", "LIDAR_TOP"}.issubset(set(manifest.channels))

    @pytest.mark.asyncio
    async def test_execute_end_to_end_against_real_dataset(
        self, tmp_path: Path
    ) -> None:
        store = LocalArtifactStore(root_uri=str(tmp_path))
        request = _request(
            uri=str(REAL_NUSCENES_DATAROOT),
            config={"max_source_sequences": 1},
        )

        result = await execute(
            request,
            artifact_store=store,
            raw_log_id="log-real",
            manifest_uri=store.join_uri(str(tmp_path), "raw_log.json"),
            frame_index_uri=store.join_uri(str(tmp_path), "frames.json"),
        )

        assert result.result_metadata["sequence_count"] == 1
        assert result.result_metadata["frame_count"] > 0

        manifest_on_disk = json.loads(
            await store.read_bytes(result.produced_artifacts["raw_log_manifest"].uri)
        )
        assert manifest_on_disk["sequenceCount"] == 1
