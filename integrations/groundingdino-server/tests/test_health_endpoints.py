"""Unit tests for /healthz and /readyz endpoints (model state, warmup policy)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from inference_server.main import app
from tests.conftest import make_mock_model, make_settings


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client_warmup_ok():
    mock_model = make_mock_model()
    settings = make_settings(enable_warmup=True, require_warmup_success_for_ready=False)
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client


@pytest.fixture()
def client_warmup_disabled():
    mock_model = make_mock_model()
    settings = make_settings(enable_warmup=False)
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client


@pytest.fixture()
def client_warmup_failed_lenient():
    mock_model = make_mock_model(warmup_raises=RuntimeError("GPU OOM"))
    settings = make_settings(enable_warmup=True, require_warmup_success_for_ready=False)
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client


@pytest.fixture()
def client_warmup_failed_strict():
    mock_model = make_mock_model(warmup_raises=RuntimeError("GPU OOM"))
    settings = make_settings(enable_warmup=True, require_warmup_success_for_ready=True)
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


# ── /healthz ─────────────────────────────────────────────────────────────────


def test_healthz_ok_without_model(client_no_lifespan):
    resp = client_no_lifespan.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_ok_with_model_loaded(client_warmup_ok):
    resp = client_warmup_ok.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── /readyz — model not loaded ────────────────────────────────────────────────


def test_readyz_503_when_model_not_loaded(client_no_lifespan):
    resp = client_no_lifespan.get("/readyz")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["status"] == "not_ready"
    assert detail["model_loaded"] is False
    assert detail["reason"] == "model is not loaded"


# ── /readyz — warmup success ──────────────────────────────────────────────────


def test_readyz_ready_with_warmup_success(client_warmup_ok):
    resp = client_warmup_ok.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["model_loaded"] is True
    assert body["warmup_enabled"] is True
    assert body["warmup_completed"] is True
    assert body["warmup_succeeded"] is True
    assert body["warmup_elapsed_ms"] is not None
    assert body["warmup_error"] is None
    assert body["device"] == "cpu"
    assert body["model_id"] == "IDEA-Research/grounding-dino-tiny"


# ── /readyz — warmup disabled ─────────────────────────────────────────────────


def test_readyz_ready_with_warmup_disabled(client_warmup_disabled):
    resp = client_warmup_disabled.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["model_loaded"] is True
    assert body["warmup_enabled"] is False
    assert body["warmup_completed"] is False
    assert body["warmup_succeeded"] is None


# ── /readyz — warmup failure, lenient policy ──────────────────────────────────


def test_readyz_ready_when_warmup_failed_lenient(client_warmup_failed_lenient):
    resp = client_warmup_failed_lenient.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["warmup_enabled"] is True
    assert body["warmup_completed"] is True
    assert body["warmup_succeeded"] is False
    assert "GPU OOM" in body["warmup_error"]


# ── /readyz — warmup failure, strict policy ───────────────────────────────────


def test_readyz_503_when_warmup_failed_strict(client_warmup_failed_strict):
    resp = client_warmup_failed_strict.get("/readyz")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["status"] == "not_ready"
    assert detail["model_loaded"] is True
    assert detail["warmup_succeeded"] is False
    assert detail["reason"] == "warmup failed"
    assert "GPU OOM" in detail["warmup_error"]
