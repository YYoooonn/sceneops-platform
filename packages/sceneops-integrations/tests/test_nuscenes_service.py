"""Tests for the nuScenes integration HTTP service (SceneOps V2 Request
4.6A): ``sceneops_integrations.nuscenes.service``.

Covers:
- GET /health returns 200 with no ArtifactStore/DB touch;
- POST /execute: success returns 200 + a valid IntegrationResult, with
  raw_log_id/manifest_uri/frame_index_uri taken from query params;
- POST /execute: a runtime-level rejection (IntegrationRuntimeError, e.g.
  unsupported operation/format) returns 422 with a readable detail, never
  a 200 wrapping a failure;
- POST /execute: missing required query params / a malformed request body
  are both rejected (422) by FastAPI's own validation, before execute()
  ever runs;
- the service module never imports sceneops-db/celery/nuscenes at import
  time (same DB-free/Celery-free/lazy-SDK guarantee as runtime.py itself).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sceneops_storage.backends.local import LocalArtifactStore

from sceneops_integrations.nuscenes.service import app


def _mock_nusc_empty() -> MagicMock:
    mock_nusc = MagicMock()
    mock_nusc.scene = []
    return mock_nusc


def _request_payload(**overrides) -> dict:
    payload = {
        "operation": "ingest",
        "externalRef": {
            "format": "nuscenes",
            "formatVersion": "v1.0-mini",
            "uri": "/data/raw/nuscenes",
        },
        "canonicalRef": {"datasetId": "d", "datasetVersion": "v1"},
    }
    payload.update(overrides)
    return payload


# ── /health ───────────────────────────────────────────────────────────────────


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── /execute: success ────────────────────────────────────────────────────────


class TestExecuteSuccess:
    def test_success_returns_200_and_produced_artifacts(self, tmp_path) -> None:
        client = TestClient(app)
        manifest_uri = str(tmp_path / "raw_log.json")
        frame_index_uri = str(tmp_path / "frames.json")

        with (
            patch(
                "sceneops_integrations.nuscenes.service.build_artifact_store",
                return_value=LocalArtifactStore(root_uri=str(tmp_path)),
            ),
            patch("nuscenes.nuscenes.NuScenes", return_value=_mock_nusc_empty()),
        ):
            response = client.post(
                "/execute",
                params={
                    "raw_log_id": "log-1",
                    "manifest_uri": manifest_uri,
                    "frame_index_uri": frame_index_uri,
                },
                json=_request_payload(),
            )

        assert response.status_code == 200
        body = response.json()
        assert set(body["producedArtifacts"]) == {
            "raw_log_manifest",
            "raw_log_frame_index",
        }
        assert body["producedArtifacts"]["raw_log_manifest"]["uri"] == manifest_uri


# ── /execute: runtime-level rejection ───────────────────────────────────────


class TestExecuteRuntimeRejection:
    def test_unsupported_operation_returns_422_with_detail(self, tmp_path) -> None:
        client = TestClient(app)
        with patch(
            "sceneops_integrations.nuscenes.service.build_artifact_store",
            return_value=LocalArtifactStore(root_uri=str(tmp_path)),
        ):
            response = client.post(
                "/execute",
                params={
                    "raw_log_id": "log-1",
                    "manifest_uri": "x",
                    "frame_index_uri": "y",
                },
                json=_request_payload(
                    operation="export",
                    canonicalInputs={
                        "x": {
                            "kind": "learning_data_export_manifest",
                            "uri": "s3://x",
                        }
                    },
                ),
            )

        assert response.status_code == 422
        assert "unsupported operation" in response.json()["detail"]

    def test_missing_format_version_returns_422(self, tmp_path) -> None:
        client = TestClient(app)
        with patch(
            "sceneops_integrations.nuscenes.service.build_artifact_store",
            return_value=LocalArtifactStore(root_uri=str(tmp_path)),
        ):
            response = client.post(
                "/execute",
                params={
                    "raw_log_id": "log-1",
                    "manifest_uri": "x",
                    "frame_index_uri": "y",
                },
                json=_request_payload(
                    externalRef={
                        "format": "nuscenes",
                        "formatVersion": "",
                        "uri": "/data/raw/nuscenes",
                    }
                ),
            )

        assert response.status_code == 422
        assert "format_version" in response.json()["detail"]


# ── /execute: FastAPI-level validation ──────────────────────────────────────


class TestExecuteValidation:
    def test_missing_required_query_params_rejected(self) -> None:
        client = TestClient(app)
        response = client.post("/execute", json=_request_payload())
        assert response.status_code == 422

    def test_malformed_body_rejected(self) -> None:
        client = TestClient(app)
        response = client.post(
            "/execute",
            params={"raw_log_id": "x", "manifest_uri": "y", "frame_index_uri": "z"},
            json={"operation": "not-a-real-operation"},
        )
        assert response.status_code == 422


# ── module import boundary ───────────────────────────────────────────────────


def test_service_module_imports_no_db_celery_or_nuscenes() -> None:
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import sceneops_integrations.nuscenes.service; "
            "loaded = set(sys.modules); "
            "assert not any(n.startswith('sqlalchemy') for n in loaded); "
            "assert not any(n.startswith('celery') for n in loaded); "
            "assert not any(n.startswith('nuscenes') for n in loaded)",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
