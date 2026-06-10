"""Tests for /v1/detect endpoint behavior: resolver integration, error mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import inference_server.main as main_module
from inference_server.main import app
from tests.conftest import make_mock_model, make_mock_resolver, make_settings


# ── fixtures ──────────────────────────────────────────────────────────────────


def _client(mock_model, mock_resolver, settings=None, raise_server_exceptions=True):
    """Return a context manager that yields a TestClient with lifespan."""
    s = settings or make_settings()
    return (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=s),
    )


# ── image_uri is passed to the resolver ──────────────────────────────────────


def test_resolver_receives_image_uri():
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver()
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app) as client:
            client.post(
                "/v1/detect",
                json={"image_uri": "file:///data/raw/img.jpg"},
            )

    mock_resolver.resolve.assert_called_once_with("file:///data/raw/img.jpg")


def test_image_path_key_not_accepted():
    """Sending image_path without image_uri must return 422."""
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver()
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/detect",
                json={"image_path": "/data/raw/img.jpg"},
            )

    assert resp.status_code == 422


# ── resolver result is forwarded to detect_image ─────────────────────────────


def test_resolver_image_forwarded_to_detect_image():
    fake_pil = MagicMock(name="pil_image")
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver(resolve_return=fake_pil)
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app) as client:
            client.post(
                "/v1/detect",
                json={"image_uri": "file:///data/raw/img.jpg"},
            )

    called_with = mock_model.detect_image.call_args
    assert called_with.args[0] is fake_pil


# ── error mapping ──────────────────────────────────────────────────────────────


def test_file_not_found_returns_422():
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver(resolve_raises=FileNotFoundError("not found"))
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/detect",
                json={"image_uri": "file:///data/raw/missing.jpg"},
            )

    assert resp.status_code == 422


def test_outside_allowed_roots_returns_403():
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver(
        resolve_raises=PermissionError("outside allowed roots")
    )
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/detect",
                json={"image_uri": "file:///etc/passwd"},
            )

    assert resp.status_code == 403


def test_unsupported_uri_scheme_returns_422():
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver(
        resolve_raises=ValueError("Unsupported image URI scheme")
    )
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/detect",
                json={"image_uri": "s3://bucket/img.jpg"},
            )

    assert resp.status_code == 422


def test_model_runtime_error_returns_500():
    mock_model = make_mock_model(detect_image_raises=RuntimeError("CUDA OOM"))
    mock_resolver = make_mock_resolver()
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/detect",
                json={"image_uri": "file:///data/raw/img.jpg"},
            )

    assert resp.status_code == 500


# ── active_requests recovery ──────────────────────────────────────────────────


def test_active_requests_decremented_after_permission_error():
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver(
        resolve_raises=PermissionError("outside allowed roots")
    )
    settings = make_settings()

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            client.post("/v1/detect", json={"image_uri": "file:///etc/passwd"})
            assert main_module._concurrency.active_requests == 0


# ── server-side defaults ──────────────────────────────────────────────────────


def test_server_default_thresholds_applied_when_not_in_request():
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver()
    settings = make_settings(box_threshold=0.5, text_threshold=0.4, max_image_size=640)

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app) as client:
            client.post("/v1/detect", json={"image_uri": "file:///data/raw/img.jpg"})

    kwargs = mock_model.detect_image.call_args.kwargs
    assert kwargs["box_threshold"] == 0.5
    assert kwargs["text_threshold"] == 0.4
    assert kwargs["max_image_size"] == 640


def test_per_request_thresholds_override_server_defaults():
    mock_model = make_mock_model()
    mock_resolver = make_mock_resolver()
    settings = make_settings(box_threshold=0.35)

    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app) as client:
            client.post(
                "/v1/detect",
                json={"image_uri": "file:///data/raw/img.jpg", "box_threshold": 0.6},
            )

    kwargs = mock_model.detect_image.call_args.kwargs
    assert kwargs["box_threshold"] == 0.6
