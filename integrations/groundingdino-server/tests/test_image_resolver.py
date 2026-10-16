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
    resolver = _resolver("/data/artifacts")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("gs://bucket/key/img.jpg")


# ── s3 ─────────────────────────────────────────────────────────────────────────


def _png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("L", (2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


def _s3_resolver(s3_client) -> ImageResolver:
    return ImageResolver(allowed_roots=["/data/artifacts"], s3_client=s3_client)


def test_s3_uri_without_configured_client_raises():
    with pytest.raises(RuntimeError, match="S3 image resolution is not configured"):
        _resolver("/data/artifacts").resolve("s3://bucket/key/img.jpg")


def test_s3_uri_reads_object_and_converts_to_rgb():
    s3_client = MagicMock()
    s3_client.get_object.return_value = {"Body": MagicMock(read=lambda: _png_bytes())}

    image = _s3_resolver(s3_client).resolve("s3://bucket/key/img.png")

    s3_client.get_object.assert_called_once_with(Bucket="bucket", Key="key/img.png")
    assert image.mode == "RGB"
    assert image.size == (2, 3)


def test_s3_missing_object_raises_file_not_found():
    from botocore.exceptions import ClientError

    s3_client = MagicMock()
    s3_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "GetObject"
    )

    with pytest.raises(FileNotFoundError, match="S3 image not found"):
        _s3_resolver(s3_client).resolve("s3://bucket/missing.png")


def test_s3_uri_without_key_raises_value_error():
    with pytest.raises(ValueError, match="Invalid S3 image URI"):
        _s3_resolver(MagicMock()).resolve("s3://bucket/")


def test_http_scheme_raises_value_error():
    resolver = _resolver("/data/artifacts")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("https://example.com/img.jpg")


def test_bare_path_raises_value_error():
    resolver = _resolver("/data/artifacts")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("/data/artifacts/img.jpg")


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
    resolver = _resolver("/data/artifacts")
    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve("file:///etc/passwd")


def test_multiple_allowed_roots(tmp_path):
    root_a = tmp_path / "inputs"
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


# ── default configuration ──────────────────────────────────────────────────────


def test_default_allowed_roots_exclude_raw_source():
    from inference_server.config import InferenceServerSettings

    roots = InferenceServerSettings().allowed_file_roots

    assert roots == ["/data/artifacts"]
    resolver = _resolver(*roots)
    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve("file:///data/raw/nuscenes/samples/CAM_FRONT/img.jpg")
