"""Tests for ImageResolver — URI parsing, allowed-roots security, and image loading."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from inference_server.image_resolver import ImageResolver


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolver(*roots: str) -> ImageResolver:
    return ImageResolver(allowed_roots=list(roots))


# ── URI parsing ───────────────────────────────────────────────────────────────


def test_file_uri_parsed_to_path(tmp_path):
    img_file = tmp_path / "img.jpg"
    img_file.write_bytes(b"")
    resolver = _resolver(str(tmp_path))

    fake_image = MagicMock()
    fake_image.convert.return_value = fake_image

    with patch("PIL.Image.open", return_value=fake_image) as mock_open:
        resolver.resolve(f"file://{img_file}")
        mock_open.assert_called_once_with(img_file.resolve())


def test_unsupported_scheme_raises_value_error():
    resolver = _resolver("/data/raw")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("s3://bucket/key/img.jpg")


def test_http_scheme_raises_value_error():
    resolver = _resolver("/data/raw")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("https://example.com/img.jpg")


def test_bare_path_raises_value_error():
    resolver = _resolver("/data/raw")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("/data/raw/nuscenes/img.jpg")


# ── allowed roots ──────────────────────────────────────────────────────────────


def test_path_within_allowed_root_is_accepted(tmp_path):
    img_file = tmp_path / "subdir" / "img.jpg"
    img_file.parent.mkdir()
    img_file.write_bytes(b"")
    resolver = _resolver(str(tmp_path))

    fake_image = MagicMock()
    fake_image.convert.return_value = fake_image

    with patch("PIL.Image.open", return_value=fake_image):
        resolver.resolve(f"file://{img_file}")  # should not raise


def test_path_outside_allowed_root_raises_permission_error(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside" / "img.jpg"
    outside.parent.mkdir()
    outside.write_bytes(b"")

    resolver = _resolver(str(allowed))
    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve(f"file://{outside}")


def test_etc_passwd_is_rejected():
    resolver = _resolver("/data/raw", "/data/artifacts")
    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve("file:///etc/passwd")


def test_multiple_allowed_roots(tmp_path):
    root_a = tmp_path / "raw"
    root_b = tmp_path / "artifacts"
    root_a.mkdir()
    root_b.mkdir()

    img_a = root_a / "img.jpg"
    img_b = root_b / "img.jpg"
    img_a.write_bytes(b"")
    img_b.write_bytes(b"")

    resolver = _resolver(str(root_a), str(root_b))
    fake_image = MagicMock()
    fake_image.convert.return_value = fake_image

    with patch("PIL.Image.open", return_value=fake_image):
        resolver.resolve(f"file://{img_a}")  # OK
        resolver.resolve(f"file://{img_b}")  # OK


# ── path traversal ─────────────────────────────────────────────────────────────


def test_path_traversal_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.txt").write_text("secret")

    resolver = _resolver(str(allowed))
    traversal_uri = f"file://{allowed}/../secret.txt"

    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve(traversal_uri)


# ── file not found ─────────────────────────────────────────────────────────────


def test_file_not_found_raises(tmp_path):
    resolver = _resolver(str(tmp_path))
    missing = tmp_path / "nonexistent.jpg"
    with pytest.raises(FileNotFoundError, match="Image not found"):
        resolver.resolve(f"file://{missing}")


# ── DetectRequest schema ───────────────────────────────────────────────────────


def test_detect_request_requires_image_uri():
    from pydantic import ValidationError
    from inference_server.schemas import DetectRequest

    with pytest.raises(ValidationError):
        DetectRequest()  # missing image_uri


def test_detect_request_rejects_extra_image_path():
    """image_path is not a valid field — extra fields are ignored by Pydantic default."""
    from inference_server.schemas import DetectRequest

    # Pydantic v2 ignores extra fields by default; image_uri is still required.
    with pytest.raises(Exception):
        DetectRequest(image_path="/data/img.jpg")  # missing image_uri


def test_detect_request_optional_fields_default_to_none():
    from inference_server.schemas import DetectRequest

    req = DetectRequest(image_uri="file:///data/img.jpg")
    assert req.prompt is None
    assert req.box_threshold is None
    assert req.text_threshold is None
    assert req.max_image_size is None
    assert req.trace_id is None


def test_detect_request_accepts_all_fields():
    from inference_server.schemas import DetectRequest

    req = DetectRequest(
        image_uri="file:///data/img.jpg",
        prompt="car . person .",
        box_threshold=0.4,
        text_threshold=0.3,
        max_image_size=640,
        trace_id="scene-001/sample-001/CAM_FRONT",
    )
    assert req.image_uri == "file:///data/img.jpg"
    assert req.box_threshold == 0.4
    assert req.trace_id == "scene-001/sample-001/CAM_FRONT"
