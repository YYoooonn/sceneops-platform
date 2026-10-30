"""Tests for /v1/detect endpoint behavior: resolver integration, error mapping."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import inference_server.main as main_module
from inference_server.main import app
from tests.conftest import (
    PIN,
    detect_body,
    make_mock_model,
    make_mock_resolver,
    make_settings,
)


# ── fixtures ──────────────────────────────────────────────────────────────────


def _client(mock_model, mock_resolver, settings=None, raise_server_exceptions=True):
    """Return a context manager that yields a TestClient with lifespan."""
    s = settings or make_settings()
    return (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.ImageResolver", return_value=mock_resolver),
        patch("inference_server.main.get_settings", return_value=s),
    )


# ── image_uri and its pin are passed to the resolver ─────────────────────────


def test_resolver_receives_image_uri_and_pin():
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
                json=detect_body("file:///data/artifacts/img.jpg"),
            )

    mock_resolver.resolve.assert_called_once_with(
        "file:///data/artifacts/img.jpg",
        checksum=PIN["image_checksum"],
        size_bytes=PIN["image_size_bytes"],
    )


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
                json={"image_path": "/data/artifacts/img.jpg"},
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
                json=detect_body("file:///data/artifacts/img.jpg"),
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
                json=detect_body("file:///data/artifacts/missing.jpg"),
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
                json=detect_body("file:///etc/passwd"),
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
                json=detect_body("s3://bucket/img.jpg"),
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
                json=detect_body("file:///data/artifacts/img.jpg"),
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
            client.post("/v1/detect", json=detect_body("file:///etc/passwd"))
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
            client.post(
                "/v1/detect", json=detect_body("file:///data/artifacts/img.jpg")
            )

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
                json=detect_body("file:///data/artifacts/img.jpg", box_threshold=0.6),
            )

    kwargs = mock_model.detect_image.call_args.kwargs
    assert kwargs["box_threshold"] == 0.6


# ── verified images through the real resolver ─────────────────────────────────


def _png(color=0) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 6), color=(color, color, color)).save(buffer, format="PNG")
    return buffer.getvalue()


def _pinned_body(image_file, data: bytes) -> dict:
    import hashlib

    return {
        "image_uri": f"file://{image_file}",
        "image_checksum": f"sha256:{hashlib.sha256(data).hexdigest()}",
        "image_size_bytes": len(data),
    }


def _post_with_real_resolver(tmp_path, body, mock_model):
    from inference_server.image_resolver import ImageResolver

    settings = make_settings(allowed_file_roots=[str(tmp_path)])
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch(
            "inference_server.main.ImageResolver",
            side_effect=lambda **kw: ImageResolver(**kw),
        ),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/v1/detect", json=body)
            assert main_module._concurrency.active_requests == 0
            return response


def test_valid_pinned_image_reaches_the_model(tmp_path):
    data = _png(color=10)
    image_file = tmp_path / "img.png"
    image_file.write_bytes(data)
    detections = [{"category_name": "car", "score": 0.9, "bbox_2d": [1, 1, 4, 4]}]
    mock_model = make_mock_model(detect_image_return=(detections, 3.0))

    resp = _post_with_real_resolver(
        tmp_path, _pinned_body(image_file, data), mock_model
    )

    assert resp.status_code == 200
    assert resp.json()["detections"] == detections
    image = mock_model.detect_image.call_args.args[0]
    assert (image.mode, image.size, image.getpixel((0, 0))) == (
        "RGB",
        (8, 6),
        (10, 10, 10),
    )


def test_corrupt_image_is_refused_without_invoking_the_model(tmp_path):
    data = _png()
    corrupt = bytearray(data)
    corrupt[-1] ^= 0x01
    image_file = tmp_path / "img.png"
    image_file.write_bytes(bytes(corrupt))
    mock_model = make_mock_model()

    resp = _post_with_real_resolver(
        tmp_path, _pinned_body(image_file, data), mock_model
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "image_integrity_mismatch"
    mock_model.detect_image.assert_not_called()


def test_size_mismatch_is_refused_without_invoking_the_model(tmp_path):
    data = _png()
    image_file = tmp_path / "img.png"
    image_file.write_bytes(data + b"\0")
    mock_model = make_mock_model()

    resp = _post_with_real_resolver(
        tmp_path, _pinned_body(image_file, data), mock_model
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "image_integrity_mismatch"
    mock_model.detect_image.assert_not_called()


def test_missing_image_is_coded_not_found(tmp_path):
    mock_model = make_mock_model()

    resp = _post_with_real_resolver(
        tmp_path, _pinned_body(tmp_path / "missing.png", _png()), mock_model
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "image_not_found"
    mock_model.detect_image.assert_not_called()


def test_undecodable_verified_image_is_coded_decode_failed(tmp_path):
    data = b"not an image"
    image_file = tmp_path / "img.png"
    image_file.write_bytes(data)
    mock_model = make_mock_model()

    resp = _post_with_real_resolver(
        tmp_path, _pinned_body(image_file, data), mock_model
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "image_decode_failed"
    mock_model.detect_image.assert_not_called()


def test_outside_allowed_roots_is_coded_access_denied(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    data = _png()
    image_file = tmp_path / "img.png"
    image_file.write_bytes(data)
    mock_model = make_mock_model()

    resp = _post_with_real_resolver(allowed, _pinned_body(image_file, data), mock_model)

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "image_access_denied"
    mock_model.detect_image.assert_not_called()


def test_request_without_a_pin_is_refused(tmp_path):
    data = _png()
    image_file = tmp_path / "img.png"
    image_file.write_bytes(data)
    mock_model = make_mock_model()

    resp = _post_with_real_resolver(
        tmp_path, {"image_uri": f"file://{image_file}"}, mock_model
    )

    assert resp.status_code == 422
    missing = {tuple(error["loc"]) for error in resp.json()["detail"]}
    assert missing == {("body", "image_checksum"), ("body", "image_size_bytes")}
    mock_model.detect_image.assert_not_called()


def test_model_value_error_is_a_model_error_not_an_image_error(tmp_path):
    data = _png()
    image_file = tmp_path / "img.png"
    image_file.write_bytes(data)
    mock_model = make_mock_model(detect_image_raises=ValueError("bad tensor"))

    resp = _post_with_real_resolver(
        tmp_path, _pinned_body(image_file, data), mock_model
    )

    assert resp.status_code == 500
    assert "code" not in resp.json()
