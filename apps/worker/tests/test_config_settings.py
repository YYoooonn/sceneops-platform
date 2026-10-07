"""Tests for config: ArtifactSettings, InputSourceSettings, WorkerSettings."""

from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactBackend
from sceneops_core.config import ArtifactSettings, InputSourceSettings, StorageSettings
from sceneops_worker.config import WorkerSettings


class TestArtifactSettings:
    def test_root_uri_default(self):
        s = ArtifactSettings()
        assert s.root_uri == "/data/artifacts"

    def test_dataset_root_uri(self):
        s = ArtifactSettings()
        assert s.dataset_root_uri == "/data/artifacts/datasets"

    def test_run_root_uri(self):
        s = ArtifactSettings()
        assert s.run_root_uri == "/data/artifacts/runs"

    def test_backend_default(self):
        s = ArtifactSettings()
        assert s.backend == ArtifactBackend.LOCAL

    def test_override_root_uri(self):
        s = ArtifactSettings(root_uri="s3://sceneops/artifacts")
        assert s.root_uri == "s3://sceneops/artifacts"
        assert s.dataset_root_uri == "s3://sceneops/artifacts/datasets"

    def test_is_storage_settings_subclass(self):
        assert issubclass(ArtifactSettings, StorageSettings)


class TestInputSourceSettings:
    def test_root_uri_default(self):
        s = InputSourceSettings()
        assert s.root_uri == "/data/inputs"

    def test_backend_default(self):
        s = InputSourceSettings()
        assert s.backend == ArtifactBackend.LOCAL

    def test_override_root_uri(self):
        s = InputSourceSettings(root_uri="s3://sceneops/raw")
        assert s.root_uri == "s3://sceneops/raw"

    def test_is_storage_settings_subclass(self):
        assert issubclass(InputSourceSettings, StorageSettings)

    def test_no_dataset_prefix_fields(self):
        s = InputSourceSettings()
        assert not hasattr(s, "dataset_prefix")
        assert not hasattr(s, "run_prefix")


class TestWorkerSettingsInputSource:
    def test_input_source_field_exists(self):
        s = WorkerSettings()
        assert hasattr(s, "input_source")
        assert isinstance(s.input_source, InputSourceSettings)

    def test_env_override_input_source_root_uri(self, monkeypatch):
        monkeypatch.setenv(
            "SCENEOPS_WORKER_INPUT_SOURCE__ROOT_URI", "/mnt/datasets/input"
        )
        s = WorkerSettings()
        assert s.input_source.root_uri == "/mnt/datasets/input"

    def test_env_override_input_source_backend(self, monkeypatch):
        monkeypatch.setenv("SCENEOPS_WORKER_INPUT_SOURCE__BACKEND", "minio")
        s = WorkerSettings()
        assert s.input_source.backend == ArtifactBackend.MINIO

    def test_artifact_root_uri_default(self):
        # _env_file=None: this asserts the schema's declared default, not
        # whatever this developer's real .env.local happens to contain
        # (WorkerSettings reads .env.local by design — see config.py).
        s = WorkerSettings(_env_file=None)
        assert s.artifact.root_uri == "/data/artifacts"

    def test_artifact_root_uri_unchanged_by_input_source(self):
        # Ensure the two settings are independent.
        s = WorkerSettings()
        assert s.artifact.root_uri != s.input_source.root_uri
