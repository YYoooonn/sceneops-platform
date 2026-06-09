"""Tests for R1 config cleanup: ArtifactSettings, RawSourceSettings, WorkerSettings."""

from __future__ import annotations

from sceneops_core.artifacts.schemas import ArtifactBackend
from sceneops_core.config import ArtifactSettings, RawSourceSettings, StorageSettings
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

    def test_model_root_uri(self):
        s = ArtifactSettings()
        assert s.model_root_uri == "/data/artifacts/models"

    def test_backend_default(self):
        s = ArtifactSettings()
        assert s.backend == ArtifactBackend.LOCAL

    def test_override_root_uri(self):
        s = ArtifactSettings(root_uri="s3://sceneops/artifacts")
        assert s.root_uri == "s3://sceneops/artifacts"
        assert s.dataset_root_uri == "s3://sceneops/artifacts/datasets"

    def test_is_storage_settings_subclass(self):
        assert issubclass(ArtifactSettings, StorageSettings)


class TestRawSourceSettings:
    def test_root_uri_default(self):
        s = RawSourceSettings()
        assert s.root_uri == "/data/raw/nuscenes"

    def test_backend_default(self):
        s = RawSourceSettings()
        assert s.backend == ArtifactBackend.LOCAL

    def test_override_root_uri(self):
        s = RawSourceSettings(root_uri="s3://sceneops/raw/nuscenes")
        assert s.root_uri == "s3://sceneops/raw/nuscenes"

    def test_is_storage_settings_subclass(self):
        assert issubclass(RawSourceSettings, StorageSettings)

    def test_no_dataset_prefix_fields(self):
        s = RawSourceSettings()
        assert not hasattr(s, "dataset_prefix")
        assert not hasattr(s, "run_prefix")
        assert not hasattr(s, "model_prefix")


class TestWorkerSettingsRawSource:
    def test_raw_source_field_exists(self):
        s = WorkerSettings()
        assert hasattr(s, "raw_source")
        assert isinstance(s.raw_source, RawSourceSettings)

    def test_raw_source_root_uri_property(self):
        s = WorkerSettings()
        assert s.raw_source_root_uri == "/data/raw/nuscenes"

    def test_raw_source_root_uri_matches_field(self):
        s = WorkerSettings()
        assert s.raw_source_root_uri == s.raw_source.root_uri

    def test_env_override_raw_source_root_uri(self, monkeypatch):
        monkeypatch.setenv(
            "SCENEOPS_WORKER_RAW_SOURCE__ROOT_URI", "/mnt/datasets/nuscenes"
        )
        s = WorkerSettings()
        assert s.raw_source.root_uri == "/mnt/datasets/nuscenes"
        assert s.raw_source_root_uri == "/mnt/datasets/nuscenes"

    def test_env_override_raw_source_backend(self, monkeypatch):
        monkeypatch.setenv("SCENEOPS_WORKER_RAW_SOURCE__BACKEND", "minio")
        s = WorkerSettings()
        assert s.raw_source.backend == ArtifactBackend.MINIO

    def test_artifact_root_uri_default(self):
        s = WorkerSettings()
        assert s.artifact.root_uri == "/data/artifacts"

    def test_artifact_root_uri_unchanged_by_raw_source(self):
        # Ensure the two settings are independent.
        s = WorkerSettings()
        assert s.artifact.root_uri != s.raw_source.root_uri
