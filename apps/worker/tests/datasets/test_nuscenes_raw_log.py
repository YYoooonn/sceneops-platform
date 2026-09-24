"""Tests for build_nuscenes_ingest_request / build_nuscenes_http_config /
build_nuscenes_container_config (SceneOps V2 Request 4.6/4.6A): the
nuScenes-specific glue between BuildScenesJobHandler and the generic
IntegrationExecutor.

Covers:
- build_nuscenes_ingest_request: operation/format/canonical identity,
  max_source_sequences only present in config when provided;
- build_nuscenes_http_config (PRODUCTION, Request 4.6A): base_url from
  settings.integration_execution.nuscenes_service_url, raw_log_id/
  manifest_uri/frame_index_uri as extra_query_params;
- build_nuscenes_container_config (LOCAL/DEV ONLY): image/network/volumes/
  extra_args wiring from WorkerSettings, ArtifactSettings translated into
  container env, and the clear failure when host_data_root isn't
  configured (no silent fallback to a guessed path).
"""

from __future__ import annotations

import pytest
from sceneops_core.config import (
    ArtifactBackend,
    ArtifactSettings,
    IntegrationExecutionSettings,
)
from sceneops_core.integration_runtime import IntegrationOperation

from sceneops_worker.config import WorkerSettings
from sceneops_worker.datasets.ingestion.nuscenes_raw_log import (
    build_nuscenes_container_config,
    build_nuscenes_http_config,
    build_nuscenes_ingest_request,
)


# ── build_nuscenes_ingest_request ───────────────────────────────────────────


class TestBuildNuscenesIngestRequest:
    def test_operation_and_format(self) -> None:
        request = build_nuscenes_ingest_request(
            dataset_id="ds-001",
            dataset_version="v1",
            source_root_uri="/data/raw/nuscenes",
            source_format_version="v1.0-mini",
        )
        assert request.operation is IntegrationOperation.INGEST
        assert request.external_ref.format == "nuscenes"
        assert request.external_ref.format_version == "v1.0-mini"
        assert request.external_ref.uri == "/data/raw/nuscenes"

    def test_canonical_identity_never_conflated_with_source_version(self) -> None:
        """dataset_version (canonical) and source_format_version (nuScenes
        SDK's own on-disk version) must land in different fields -- Request
        3.2B/3.2B.1's separation, still true through the generic executor."""
        request = build_nuscenes_ingest_request(
            dataset_id="ds-001",
            dataset_version="test-v1",
            source_root_uri="/data/raw/nuscenes",
            source_format_version="v1.0-mini",
        )
        assert request.canonical_ref.dataset_version == "test-v1"
        assert request.external_ref.format_version == "v1.0-mini"
        assert (
            request.canonical_ref.dataset_version != request.external_ref.format_version
        )

    def test_max_source_sequences_omitted_when_none(self) -> None:
        request = build_nuscenes_ingest_request(
            dataset_id="ds-001",
            dataset_version="v1",
            source_root_uri="/data/raw/nuscenes",
            source_format_version="v1.0-mini",
        )
        assert "max_source_sequences" not in request.config

    def test_max_source_sequences_present_when_given(self) -> None:
        request = build_nuscenes_ingest_request(
            dataset_id="ds-001",
            dataset_version="v1",
            source_root_uri="/data/raw/nuscenes",
            source_format_version="v1.0-mini",
            max_source_sequences=3,
        )
        assert request.config["max_source_sequences"] == 3


# ── build_nuscenes_container_config ─────────────────────────────────────────


def _settings(*, host_data_root: str | None = "/host/data") -> WorkerSettings:
    return WorkerSettings(
        artifact=ArtifactSettings(
            backend=ArtifactBackend.MINIO,
            root_uri="s3://sceneops/artifacts",
            endpoint_url="http://minio:9000",
            access_key_id="minioadmin",
            secret_access_key="minioadmin",
        ),
        integration_execution=IntegrationExecutionSettings(
            nuscenes_service_url="http://nuscenes-integration:8080",
            nuscenes_image="sceneops-platform/nuscenes-integration:local",
            docker_network="sceneops-network",
            host_data_root=host_data_root,
            io_root_uri="/data/runs/integration-exec",
        ),
    )


# ── build_nuscenes_http_config (PRODUCTION, Request 4.6A) ───────────────────


class TestBuildNuscenesHttpConfig:
    def test_base_url_from_settings(self) -> None:
        config = build_nuscenes_http_config(
            settings=_settings(),
            raw_log_id="log-001",
            manifest_uri="s3://x/manifest.json",
            frame_index_uri="s3://x/frames.json",
        )
        assert config.base_url == "http://nuscenes-integration:8080"

    def test_extra_query_params_carry_raw_log_id_and_destination_uris(self) -> None:
        config = build_nuscenes_http_config(
            settings=_settings(),
            raw_log_id="log-001",
            manifest_uri="s3://x/manifest.json",
            frame_index_uri="s3://x/frames.json",
        )
        assert config.extra_query_params == {
            "raw_log_id": "log-001",
            "manifest_uri": "s3://x/manifest.json",
            "frame_index_uri": "s3://x/frames.json",
        }

    def test_no_host_data_root_required(self) -> None:
        """Unlike the container backend, the HTTP backend needs no
        Docker-outside-of-Docker path translation at all."""
        config = build_nuscenes_http_config(
            settings=_settings(host_data_root=None),
            raw_log_id="log-001",
            manifest_uri="s3://x/manifest.json",
            frame_index_uri="s3://x/frames.json",
        )
        assert config.base_url == "http://nuscenes-integration:8080"


# ── build_nuscenes_container_config (LOCAL/DEV ONLY) ─────────────────────────


class TestBuildNuscenesContainerConfig:
    def test_image_and_network_from_settings(self) -> None:
        config = build_nuscenes_container_config(
            settings=_settings(),
            raw_log_id="log-001",
            manifest_uri="s3://sceneops/artifacts/raw/log-001/raw_log.json",
            frame_index_uri="s3://sceneops/artifacts/raw/log-001/frames.json",
        )
        assert config.image == "sceneops-platform/nuscenes-integration:local"
        assert config.network == "sceneops-network"

    def test_volume_maps_host_data_root_to_slash_data(self) -> None:
        config = build_nuscenes_container_config(
            settings=_settings(host_data_root="/host/data"),
            raw_log_id="log-001",
            manifest_uri="s3://x/manifest.json",
            frame_index_uri="s3://x/frames.json",
        )
        assert config.volumes == (("/host/data", "/data"),)

    def test_io_dir_scoped_by_raw_log_id(self) -> None:
        config = build_nuscenes_container_config(
            settings=_settings(),
            raw_log_id="log-A",
            manifest_uri="s3://x/manifest.json",
            frame_index_uri="s3://x/frames.json",
        )
        assert config.io_dir == "/data/runs/integration-exec/log-A"

    def test_extra_args_carry_raw_log_id_and_destination_uris(self) -> None:
        config = build_nuscenes_container_config(
            settings=_settings(),
            raw_log_id="log-001",
            manifest_uri="s3://x/manifest.json",
            frame_index_uri="s3://x/frames.json",
        )
        assert config.extra_args == (
            "--raw-log-id",
            "log-001",
            "--manifest-uri",
            "s3://x/manifest.json",
            "--frame-index-uri",
            "s3://x/frames.json",
        )

    def test_env_translated_from_worker_artifact_settings(self) -> None:
        config = build_nuscenes_container_config(
            settings=_settings(),
            raw_log_id="log-001",
            manifest_uri="s3://x/manifest.json",
            frame_index_uri="s3://x/frames.json",
        )
        assert config.env["SCENEOPS_INTEGRATION_ARTIFACT__BACKEND"] == "minio"
        assert (
            config.env["SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI"]
            == "s3://sceneops/artifacts"
        )
        assert (
            config.env["SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL"]
            == "http://minio:9000"
        )

    def test_missing_host_data_root_raises_clearly(self) -> None:
        with pytest.raises(ValueError, match="host_data_root"):
            build_nuscenes_container_config(
                settings=_settings(host_data_root=None),
                raw_log_id="log-001",
                manifest_uri="s3://x/manifest.json",
                frame_index_uri="s3://x/frames.json",
            )
