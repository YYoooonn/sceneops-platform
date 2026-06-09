"""Tests for NuScenesRawLogMocker object-storage guard (R4A).

Covers:
- _is_object_storage_uri: recognises s3://, gs://, gcs://, minio://, az://, abfs://
- _is_object_storage_uri: local paths return False
- build_raw_log raises NotImplementedError for all object-storage schemes
- error message includes the received URI
- build_raw_log reaches NuScenes SDK for local paths (guard does not block)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sceneops_worker.datasets.ingestion.nuscenes_raw_log import NuScenesRawLogMocker


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mocker(source_root_uri: str) -> NuScenesRawLogMocker:
    return NuScenesRawLogMocker(
        source_store=MagicMock(),
        source_root_uri=source_root_uri,
        observation_store=MagicMock(),
        required_channels={"CAM_FRONT", "LIDAR_TOP"},
    )


_BUILD_RAW_LOG_KWARGS = dict(
    dataset_id="nuscenes",
    dataset_version="v1.0-mini",
    raw_log_id="log-001",
    version_root_uri="s3://root/",
    params={},
)


# ── _is_object_storage_uri ────────────────────────────────────────────────────


class TestIsObjectStorageUri:
    @pytest.mark.parametrize(
        "uri",
        [
            "s3://sceneops/raw/nuscenes",
            "s3://bucket/path/to/data",
            "gs://bucket/raw/nuscenes",
            "gcs://bucket/raw/nuscenes",
            "minio://sceneops/raw/nuscenes",
            "az://container/raw",
            "abfs://container@account.dfs.core.windows.net/raw",
        ],
    )
    def test_object_storage_uris_return_true(self, uri: str) -> None:
        assert NuScenesRawLogMocker._is_object_storage_uri(uri) is True

    @pytest.mark.parametrize(
        "uri",
        [
            "/data/raw/nuscenes",
            "/data/raw/nuscenes/v1.0-mini",
            "file:///data/raw/nuscenes",
            "./relative/path",
            "data/raw/nuscenes",
            "",
        ],
    )
    def test_local_uris_return_false(self, uri: str) -> None:
        assert NuScenesRawLogMocker._is_object_storage_uri(uri) is False


# ── build_raw_log: object-storage guard ──────────────────────────────────────


class TestBuildRawLogObjectStorageGuard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "uri",
        [
            "s3://sceneops/raw/nuscenes",
            "gs://bucket/raw/nuscenes",
            "gcs://bucket/raw/nuscenes",
            "minio://sceneops/raw/nuscenes",
        ],
    )
    async def test_raises_not_implemented_for_object_storage(self, uri: str) -> None:
        mocker = _make_mocker(uri)
        with pytest.raises(NotImplementedError):
            await mocker.build_raw_log(**_BUILD_RAW_LOG_KWARGS)

    @pytest.mark.asyncio
    async def test_error_message_contains_received_uri(self) -> None:
        uri = "s3://sceneops/raw/nuscenes"
        mocker = _make_mocker(uri)
        with pytest.raises(NotImplementedError, match=uri):
            await mocker.build_raw_log(**_BUILD_RAW_LOG_KWARGS)

    @pytest.mark.asyncio
    async def test_error_message_explains_local_required(self) -> None:
        mocker = _make_mocker("s3://bucket/raw")
        with pytest.raises(NotImplementedError, match="local filesystem"):
            await mocker.build_raw_log(**_BUILD_RAW_LOG_KWARGS)

    @pytest.mark.asyncio
    async def test_nuscenes_sdk_not_called_for_object_storage(self) -> None:
        # NuScenes is imported lazily inside build_raw_log; patch at source module.
        mocker = _make_mocker("s3://sceneops/raw/nuscenes")
        with patch("nuscenes.nuscenes.NuScenes") as MockNuScenes:
            with pytest.raises(NotImplementedError):
                await mocker.build_raw_log(**_BUILD_RAW_LOG_KWARGS)
        MockNuScenes.assert_not_called()


# ── build_raw_log: local path reaches SDK ────────────────────────────────────


class TestBuildRawLogLocalPath:
    @pytest.mark.asyncio
    async def test_local_path_reaches_nuscenes_sdk(self) -> None:
        """Guard does not block local paths; NuScenes SDK constructor is called."""
        mocker = _make_mocker("/data/raw/nuscenes")

        mock_obs_store = AsyncMock()
        mock_obs_store.raw_log_manifest_uri = MagicMock(
            return_value="s3://root/manifest.json"
        )
        mock_obs_store.raw_frame_index_uri = MagicMock(
            return_value="s3://root/frames.json"
        )
        mock_obs_store.save_raw_log_manifest = AsyncMock()
        mock_obs_store.save_raw_frame_index = AsyncMock()

        mocker._observation_store = mock_obs_store

        mock_nusc = MagicMock()
        mock_nusc.scene = []

        with patch(
            "nuscenes.nuscenes.NuScenes",
            return_value=mock_nusc,
        ) as MockNuScenes:
            await mocker.build_raw_log(**_BUILD_RAW_LOG_KWARGS)

        MockNuScenes.assert_called_once_with(
            version="v1.0-mini",
            dataroot="/data/raw/nuscenes",
            verbose=False,
        )
