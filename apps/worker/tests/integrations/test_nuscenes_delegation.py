"""Cross-package proof that NuScenesRawLogMocker (apps/worker, kept for
BuildScenesJobHandler's RawLogAdapter contract, Request 4.5 §5) delegates
to sceneops_integrations.nuscenes.raw_log.read_nuscenes_raw_log unchanged
-- the extraction/isolation moved code, it didn't fork behavior. The
runtime's own behavior (operation guards, produced_artifacts mapping,
real-fixture parsing, ...) is covered in
packages/sceneops-integrations/tests/test_nuscenes_runtime.py; this file
only proves the two packages agree.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sceneops_storage.backends.local import LocalArtifactStore

from sceneops_integrations.nuscenes.raw_log import read_nuscenes_raw_log
from sceneops_worker.datasets.ingestion.nuscenes_raw_log import NuScenesRawLogMocker
from sceneops_worker.observations.artifacts import ObservationArtifactStore


def _mock_nusc_empty() -> MagicMock:
    mock_nusc = MagicMock()
    mock_nusc.scene = []
    return mock_nusc


class TestWorkerAdapterEquivalence:
    @pytest.mark.asyncio
    async def test_mocker_and_pure_reader_produce_identical_manifest(
        self, tmp_path: Path
    ) -> None:
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
