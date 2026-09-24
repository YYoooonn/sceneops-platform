"""Tests for ContainerIntegrationExecutor / artifact_settings_to_env
(SceneOps V2 Request 4.6): the production IntegrationExecutor backend.

Covers:
- the request is written to io_dir/request.json exactly as
  IntegrationRequest.model_dump_json(by_alias=True) would produce;
- the constructed `docker run` argv includes --rm, --network, -e per env
  entry, -v per volume, the image, --request-file/--output-file, then
  extra_args, in that order;
- a non-zero exit raises IntegrationExecutionError with the container's
  stderr in the message;
- a zero exit with no --output-file written raises IntegrationExecutionError;
- invalid JSON / a payload that doesn't validate as IntegrationResult both
  raise IntegrationExecutionError;
- a successful run returns the parsed, validated IntegrationResult;
- `docker` not being on PATH raises IntegrationExecutionError, not a bare
  FileNotFoundError;
- artifact_settings_to_env only includes non-None fields, correctly
  prefixed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.config import ArtifactBackend, ArtifactSettings
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
    IntegrationResult,
)

from sceneops_worker.integration_execution import (
    ContainerIntegrationExecutor,
    ContainerRuntimeConfig,
    IntegrationExecutionError,
    artifact_settings_to_env,
)


def _request() -> IntegrationRequest:
    return IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
        ),
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
    )


def _result_json() -> str:
    result = IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
        ),
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
        produced_artifacts={
            "raw_log_manifest": ArtifactRef(
                kind=ArtifactKind.RAW_LOG_MANIFEST, uri="s3://x/manifest.json"
            )
        },
    )
    return result.model_dump_json(by_alias=True)


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


def _config(tmp_path: Path, **overrides) -> ContainerRuntimeConfig:
    defaults = dict(
        image="sceneops-platform/nuscenes-integration:local",
        io_dir=str(tmp_path / "io"),
        volumes=(("/host/data", "/data"),),
        network="sceneops-network",
        extra_args=("--raw-log-id", "log-1"),
        env={"SCENEOPS_INTEGRATION_ARTIFACT__BACKEND": "minio"},
    )
    defaults.update(overrides)
    return ContainerRuntimeConfig(**defaults)


# ── request serialization ────────────────────────────────────────────────────


class TestRequestSerialization:
    @pytest.mark.asyncio
    async def test_request_written_to_io_dir(self, tmp_path: Path) -> None:
        request = _request()
        config = _config(tmp_path)

        async def _fake_output(*args, **kwargs):
            Path(config.io_dir, "result.json").write_text(_result_json())
            return _FakeProcess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_output):
            await ContainerIntegrationExecutor(config).execute(request)

        written = json.loads(Path(config.io_dir, "request.json").read_text())
        assert written == json.loads(request.model_dump_json(by_alias=True))


# ── docker argv construction ─────────────────────────────────────────────────


class TestDockerArgvConstruction:
    @pytest.mark.asyncio
    async def test_argv_includes_network_env_volumes_image_and_extra_args(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)
        captured: dict = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            Path(config.io_dir, "result.json").write_text(_result_json())
            return _FakeProcess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await ContainerIntegrationExecutor(config).execute(_request())

        argv = list(captured["args"])
        assert argv[0:3] == ["docker", "run", "--rm"]
        assert "--network" in argv and "sceneops-network" in argv
        assert "-e" in argv
        assert "SCENEOPS_INTEGRATION_ARTIFACT__BACKEND=minio" in argv
        assert "-v" in argv
        assert "/host/data:/data" in argv
        assert config.image in argv
        assert "--request-file" in argv
        assert "--output-file" in argv
        assert "--raw-log-id" in argv and "log-1" in argv
        # extra_args must come after --request-file/--output-file
        assert argv.index("--raw-log-id") > argv.index("--output-file")

    @pytest.mark.asyncio
    async def test_no_network_flag_when_network_is_none(self, tmp_path: Path) -> None:
        config = _config(tmp_path, network=None)
        captured: dict = {}

        async def _fake_exec(*args, **kwargs):
            captured["args"] = args
            Path(config.io_dir, "result.json").write_text(_result_json())
            return _FakeProcess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await ContainerIntegrationExecutor(config).execute(_request())

        assert "--network" not in captured["args"]


# ── failure handling ──────────────────────────────────────────────────────────


class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_with_stderr(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        async def _fake_exec(*args, **kwargs):
            return _FakeProcess(returncode=1, stderr=b"boom: bad config")

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            with pytest.raises(IntegrationExecutionError, match="boom: bad config"):
                await ContainerIntegrationExecutor(config).execute(_request())

    @pytest.mark.asyncio
    async def test_zero_exit_but_no_output_file_raises(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        async def _fake_exec(*args, **kwargs):
            return _FakeProcess(returncode=0)  # never wrote result.json

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            with pytest.raises(IntegrationExecutionError, match="wrote no"):
                await ContainerIntegrationExecutor(config).execute(_request())

    @pytest.mark.asyncio
    async def test_invalid_json_output_raises(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        async def _fake_exec(*args, **kwargs):
            Path(config.io_dir).mkdir(parents=True, exist_ok=True)
            Path(config.io_dir, "result.json").write_text("{not json")
            return _FakeProcess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            with pytest.raises(IntegrationExecutionError, match="invalid JSON"):
                await ContainerIntegrationExecutor(config).execute(_request())

    @pytest.mark.asyncio
    async def test_result_failing_validation_raises(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        async def _fake_exec(*args, **kwargs):
            Path(config.io_dir).mkdir(parents=True, exist_ok=True)
            Path(config.io_dir, "result.json").write_text(
                json.dumps({"operation": "not-a-real-operation"})
            )
            return _FakeProcess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            with pytest.raises(IntegrationExecutionError, match="does not validate"):
                await ContainerIntegrationExecutor(config).execute(_request())

    @pytest.mark.asyncio
    async def test_docker_not_found_raises_execution_error(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path)

        async def _raise(*args, **kwargs):
            raise FileNotFoundError("no such file: docker")

        with patch("asyncio.create_subprocess_exec", side_effect=_raise):
            with pytest.raises(IntegrationExecutionError, match="cannot invoke docker"):
                await ContainerIntegrationExecutor(config).execute(_request())


# ── success path ──────────────────────────────────────────────────────────────


class TestSuccessPath:
    @pytest.mark.asyncio
    async def test_returns_validated_integration_result(self, tmp_path: Path) -> None:
        config = _config(tmp_path)

        async def _fake_exec(*args, **kwargs):
            Path(config.io_dir).mkdir(parents=True, exist_ok=True)
            Path(config.io_dir, "result.json").write_text(_result_json())
            return _FakeProcess(returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            result = await ContainerIntegrationExecutor(config).execute(_request())

        assert isinstance(result, IntegrationResult)
        assert result.produced_artifacts["raw_log_manifest"].uri == (
            "s3://x/manifest.json"
        )


# ── artifact_settings_to_env ────────────────────────────────────────────────


class TestArtifactSettingsToEnv:
    def test_only_non_none_fields_included(self) -> None:
        settings = ArtifactSettings(
            backend=ArtifactBackend.LOCAL, root_uri="/data/artifacts"
        )
        env = artifact_settings_to_env(settings)
        assert env == {
            "SCENEOPS_INTEGRATION_ARTIFACT__BACKEND": "local",
            "SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI": "/data/artifacts",
        }

    def test_minio_backend_includes_all_credential_fields(self) -> None:
        settings = ArtifactSettings(
            backend=ArtifactBackend.MINIO,
            root_uri="s3://sceneops/artifacts",
            endpoint_url="http://minio:9000",
            region="ap-northeast-2",
            access_key_id="minioadmin",
            secret_access_key="minioadmin",
        )
        env = artifact_settings_to_env(settings)
        assert env == {
            "SCENEOPS_INTEGRATION_ARTIFACT__BACKEND": "minio",
            "SCENEOPS_INTEGRATION_ARTIFACT__ROOT_URI": "s3://sceneops/artifacts",
            "SCENEOPS_INTEGRATION_ARTIFACT__ENDPOINT_URL": "http://minio:9000",
            "SCENEOPS_INTEGRATION_ARTIFACT__REGION": "ap-northeast-2",
            "SCENEOPS_INTEGRATION_ARTIFACT__ACCESS_KEY_ID": "minioadmin",
            "SCENEOPS_INTEGRATION_ARTIFACT__SECRET_ACCESS_KEY": "minioadmin",
        }

    def test_never_emits_literal_none_string(self) -> None:
        settings = ArtifactSettings(
            backend=ArtifactBackend.LOCAL, root_uri="/data/artifacts"
        )
        env = artifact_settings_to_env(settings)
        assert "None" not in env.values()
