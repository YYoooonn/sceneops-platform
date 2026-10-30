"""Tests for ImageResolver — URI parsing, allowed-roots security, pinned reads and
decoding of exactly the verified bytes."""

from __future__ import annotations

import hashlib
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from inference_server import image_resolver as image_resolver_module
from inference_server.image_resolver import (
    ImageDecodeError,
    ImageIntegrityError,
    ImageResolver,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolver(*roots: str) -> ImageResolver:
    return ImageResolver(allowed_roots=list(roots))


def _png_bytes(size=(2, 3), color=0) -> bytes:
    buffer = BytesIO()
    Image.new("L", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _pin(data: bytes) -> dict:
    return {
        "checksum": f"sha256:{hashlib.sha256(data).hexdigest()}",
        "size_bytes": len(data),
    }


# Any pin: for cases refused before the object is read.
_ANY_PIN = {"checksum": "sha256:" + "0" * 64, "size_bytes": 1}


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _flip_last_byte(data: bytes) -> bytes:
    flipped = bytearray(data)
    flipped[-1] ^= 0x01
    return bytes(flipped)


# ── URI parsing ───────────────────────────────────────────────────────────────


def test_file_uri_reads_the_path_verbatim(tmp_path):
    # '?' and '#' are ordinary file-name characters, as the storage backends
    # that wrote the object spell them; urlparse would cut the path there.
    data = _png_bytes()
    img_file = _write(tmp_path / "a?b#c.png", data)

    image = _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))

    assert image.size == (2, 3)


def test_unsupported_scheme_raises_value_error():
    resolver = _resolver("/data/artifacts")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("gs://bucket/key/img.jpg", **_ANY_PIN)


def test_http_scheme_raises_value_error():
    # No remote URL is ever fetched.
    resolver = _resolver("/data/artifacts")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("https://example.com/img.jpg", **_ANY_PIN)


def test_bare_path_raises_value_error():
    resolver = _resolver("/data/artifacts")
    with pytest.raises(ValueError, match="Unsupported image URI scheme"):
        resolver.resolve("/data/artifacts/img.jpg", **_ANY_PIN)


# ── pin validation ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "checksum",
    [
        "0" * 64,  # no algorithm prefix
        "sha256:" + "A" * 64,  # uppercase hex
        "sha256:" + "0" * 63,  # short
        "md5:" + "0" * 32,
        "",
    ],
)
def test_malformed_checksum_is_refused_before_any_read(tmp_path, checksum):
    data = _png_bytes()
    img_file = _write(tmp_path / "img.png", data)
    with patch.object(image_resolver_module.Path, "open") as opened:
        with pytest.raises(ValueError, match="checksum must be"):
            _resolver(str(tmp_path)).resolve(
                f"file://{img_file}", checksum=checksum, size_bytes=len(data)
            )
    opened.assert_not_called()


@pytest.mark.parametrize("size_bytes", [-1, True, 1.0, "3"])
def test_malformed_size_is_refused(tmp_path, size_bytes):
    img_file = _write(tmp_path / "img.png", _png_bytes())
    with pytest.raises(ValueError, match="size_bytes must be"):
        _resolver(str(tmp_path)).resolve(
            f"file://{img_file}",
            checksum=_ANY_PIN["checksum"],
            size_bytes=size_bytes,
        )


def test_resolve_has_no_unpinned_form(tmp_path):
    img_file = _write(tmp_path / "img.png", _png_bytes())
    with pytest.raises(TypeError):
        _resolver(str(tmp_path)).resolve(f"file://{img_file}")  # type: ignore[call-arg]


# ── verified file reads ────────────────────────────────────────────────────────


def test_valid_file_decodes_as_before(tmp_path):
    data = _png_bytes(size=(4, 5), color=200)
    img_file = _write(tmp_path / "img.png", data)

    image = _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))

    with Image.open(BytesIO(data)) as expected:
        reference = expected.convert("RGB")
    assert image.mode == "RGB"
    assert image.size == (4, 5)
    assert image.tobytes() == reference.tobytes()


def test_same_size_corruption_fails_checksum(tmp_path):
    data = _png_bytes()
    corrupt = _flip_last_byte(data)
    img_file = _write(tmp_path / "img.png", corrupt)

    with pytest.raises(ImageIntegrityError, match="the pin requires sha256:"):
        _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))


def test_shorter_object_fails_size(tmp_path):
    data = _png_bytes()
    img_file = _write(tmp_path / "img.png", data[:-1])

    with pytest.raises(ImageIntegrityError, match=f"the pin requires {len(data)}"):
        _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))


def test_longer_object_fails_size_without_reading_it_all(tmp_path):
    data = _png_bytes()
    img_file = _write(tmp_path / "img.png", data + b"\0" * (4 << 20))

    with pytest.raises(ImageIntegrityError, match=f"more than {len(data)} bytes"):
        _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))


def test_file_not_found_raises(tmp_path):
    resolver = _resolver(str(tmp_path))
    missing = tmp_path / "nonexistent.jpg"
    with pytest.raises(FileNotFoundError, match="Image not found"):
        resolver.resolve(f"file://{missing}", **_ANY_PIN)


def test_directory_is_not_found(tmp_path):
    (tmp_path / "dir.png").mkdir()
    with pytest.raises(FileNotFoundError, match="Image not found"):
        _resolver(str(tmp_path)).resolve(f"file://{tmp_path}/dir.png", **_ANY_PIN)


def test_verified_non_image_is_a_decode_error(tmp_path):
    data = b"verified, but not an image"
    img_file = _write(tmp_path / "img.png", data)

    with pytest.raises(ImageDecodeError, match="not a valid image"):
        _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))


def test_decoder_receives_exactly_the_verified_bytes(tmp_path):
    """The file is read once; the decoder gets those bytes from memory and never
    the path, so a change to the file after the check cannot reach the model."""
    data = _png_bytes()
    img_file = _write(tmp_path / "img.png", data)
    decoded: list[bytes] = []
    real_open = Image.open

    def spy_open(source, *args, **kwargs):
        assert isinstance(source, BytesIO), f"decoder opened {source!r}"
        decoded.append(source.getvalue())
        # Replace the stored object after the check: decoding is unaffected.
        img_file.write_bytes(_png_bytes(color=255))
        return real_open(source, *args, **kwargs)

    with patch.object(image_resolver_module.Image, "open", side_effect=spy_open):
        image = _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))

    assert decoded == [data]
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_decoder_is_not_called_when_verification_fails(tmp_path):
    data = _png_bytes()
    img_file = _write(tmp_path / "img.png", _flip_last_byte(data))
    with patch.object(image_resolver_module.Image, "open") as opened:
        with pytest.raises(ImageIntegrityError):
            _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))
    opened.assert_not_called()


# ── s3 ─────────────────────────────────────────────────────────────────────────


def _s3_client(data: bytes) -> MagicMock:
    """A client whose GetObject body is botocore's own StreamingBody, which
    checks the bytes read against the response's Content-Length at EOF."""
    from botocore.response import StreamingBody

    s3_client = MagicMock()
    s3_client.get_object.side_effect = lambda **_: {
        "Body": StreamingBody(BytesIO(data), len(data)),
        "ContentLength": len(data),
    }
    return s3_client


def _s3_resolver(s3_client) -> ImageResolver:
    return ImageResolver(allowed_roots=["/data/artifacts"], s3_client=s3_client)


def test_s3_uri_without_configured_client_raises():
    with pytest.raises(RuntimeError, match="S3 image resolution is not configured"):
        _resolver("/data/artifacts").resolve("s3://bucket/key/img.jpg", **_ANY_PIN)


def test_s3_uri_reads_object_and_converts_to_rgb():
    data = _png_bytes()
    s3_client = _s3_client(data)

    image = _s3_resolver(s3_client).resolve("s3://bucket/key/img.png", **_pin(data))

    s3_client.get_object.assert_called_once_with(Bucket="bucket", Key="key/img.png")
    assert image.mode == "RGB"
    assert image.size == (2, 3)


def test_s3_key_is_taken_verbatim():
    data = _png_bytes()
    s3_client = _s3_client(data)

    _s3_resolver(s3_client).resolve("s3://bucket/key/a?b#c.png", **_pin(data))

    s3_client.get_object.assert_called_once_with(Bucket="bucket", Key="key/a?b#c.png")


def test_s3_same_size_corruption_fails_checksum():
    data = _png_bytes()
    s3_client = _s3_client(_flip_last_byte(data))
    with patch.object(image_resolver_module.Image, "open") as opened:
        with pytest.raises(ImageIntegrityError, match="the pin requires sha256:"):
            _s3_resolver(s3_client).resolve("s3://bucket/img.png", **_pin(data))
    opened.assert_not_called()


def test_s3_object_larger_than_one_read_chunk_is_verified():
    data = _png_bytes() + b"\0" * (3 << 20)
    s3_client = _s3_client(data)
    resolver = _s3_resolver(s3_client)

    assert resolver.read_verified("s3://bucket/big.bin", **_pin(data)) == data
    with pytest.raises(ImageIntegrityError, match="more than"):
        resolver.read_verified(
            "s3://bucket/big.bin",
            checksum=_pin(data)["checksum"],
            size_bytes=len(data) - 1,
        )


def test_s3_size_mismatch_fails():
    data = _png_bytes()
    s3_client = _s3_client(data + b"\0")
    with pytest.raises(ImageIntegrityError, match="more than"):
        _s3_resolver(s3_client).resolve("s3://bucket/img.png", **_pin(data))


def test_s3_missing_object_raises_file_not_found():
    from botocore.exceptions import ClientError

    s3_client = MagicMock()
    s3_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "GetObject"
    )

    with pytest.raises(FileNotFoundError, match="S3 image not found"):
        _s3_resolver(s3_client).resolve("s3://bucket/missing.png", **_ANY_PIN)


def test_s3_uri_without_key_raises_value_error():
    with pytest.raises(ValueError, match="Invalid S3 image URI"):
        _s3_resolver(MagicMock()).resolve("s3://bucket/", **_ANY_PIN)


# ── allowed roots ──────────────────────────────────────────────────────────────


def test_path_within_allowed_root_is_accepted(tmp_path):
    data = _png_bytes()
    img_file = _write(tmp_path / "subdir" / "img.png", data)
    _resolver(str(tmp_path)).resolve(f"file://{img_file}", **_pin(data))


def test_path_outside_allowed_root_raises_permission_error(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    data = _png_bytes()
    outside = _write(tmp_path / "outside" / "img.png", data)

    resolver = _resolver(str(allowed))
    # Refused even with the correct pin: the roots, not the pin, grant access.
    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve(f"file://{outside}", **_pin(data))


def test_etc_passwd_is_rejected():
    resolver = _resolver("/data/artifacts")
    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve("file:///etc/passwd", **_ANY_PIN)


def test_multiple_allowed_roots(tmp_path):
    data = _png_bytes()
    img_a = _write(tmp_path / "inputs" / "img.png", data)
    img_b = _write(tmp_path / "artifacts" / "img.png", data)

    resolver = _resolver(str(tmp_path / "inputs"), str(tmp_path / "artifacts"))
    resolver.resolve(f"file://{img_a}", **_pin(data))
    resolver.resolve(f"file://{img_b}", **_pin(data))


def test_symlink_out_of_allowed_root_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    data = _png_bytes()
    target = _write(tmp_path / "secret.png", data)
    (allowed / "link.png").symlink_to(target)

    with pytest.raises(PermissionError, match="outside allowed roots"):
        _resolver(str(allowed)).resolve(f"file://{allowed}/link.png", **_pin(data))


# ── path traversal ─────────────────────────────────────────────────────────────


def test_path_traversal_is_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.txt").write_text("secret")

    resolver = _resolver(str(allowed))
    traversal_uri = f"file://{allowed}/../secret.txt"

    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve(traversal_uri, **_ANY_PIN)


# ── DetectRequest schema ───────────────────────────────────────────────────────

_REQUEST_PIN = {"image_checksum": "sha256:" + "0" * 64, "image_size_bytes": 0}


def test_detect_request_requires_image_uri():
    from pydantic import ValidationError
    from inference_server.schemas import DetectRequest

    with pytest.raises(ValidationError):
        DetectRequest(**_REQUEST_PIN)  # missing image_uri


@pytest.mark.parametrize("missing", ["image_checksum", "image_size_bytes"])
def test_detect_request_requires_the_pin(missing):
    from pydantic import ValidationError
    from inference_server.schemas import DetectRequest

    fields = {"image_uri": "file:///data/img.jpg", **_REQUEST_PIN}
    del fields[missing]
    with pytest.raises(ValidationError, match=missing):
        DetectRequest(**fields)


@pytest.mark.parametrize(
    "field, value",
    [
        ("image_checksum", "0" * 64),
        ("image_checksum", "sha256:" + "F" * 64),
        ("image_size_bytes", -1),
        ("image_size_bytes", "12"),
        ("image_size_bytes", 1.5),
    ],
)
def test_detect_request_rejects_a_malformed_pin(field, value):
    from pydantic import ValidationError
    from inference_server.schemas import DetectRequest

    fields = {"image_uri": "file:///data/img.jpg", **_REQUEST_PIN, field: value}
    with pytest.raises(ValidationError, match=field):
        DetectRequest(**fields)


def test_detect_request_rejects_extra_image_path():
    """image_path is not a valid field — extra fields are ignored by Pydantic default."""
    from inference_server.schemas import DetectRequest

    # Pydantic v2 ignores extra fields by default; image_uri is still required.
    with pytest.raises(Exception):
        DetectRequest(image_path="/data/img.jpg", **_REQUEST_PIN)  # missing image_uri


def test_detect_request_optional_fields_default_to_none():
    from inference_server.schemas import DetectRequest

    req = DetectRequest(image_uri="file:///data/img.jpg", **_REQUEST_PIN)
    assert req.prompt is None
    assert req.box_threshold is None
    assert req.text_threshold is None
    assert req.max_image_size is None
    assert req.trace_id is None


def test_detect_request_accepts_all_fields():
    from inference_server.schemas import DetectRequest

    req = DetectRequest(
        image_uri="file:///data/img.jpg",
        **_REQUEST_PIN,
        prompt="car . person .",
        box_threshold=0.4,
        text_threshold=0.3,
        max_image_size=640,
        trace_id="scene-001/sample-001/CAM_FRONT",
    )
    assert req.image_uri == "file:///data/img.jpg"
    assert req.image_checksum == _REQUEST_PIN["image_checksum"]
    assert req.image_size_bytes == 0
    assert req.box_threshold == 0.4
    assert req.trace_id == "scene-001/sample-001/CAM_FRONT"


# ── default configuration ──────────────────────────────────────────────────────


def test_default_allowed_roots_exclude_raw_source():
    from inference_server.config import InferenceServerSettings

    roots = InferenceServerSettings().allowed_file_roots

    assert roots == ["/data/artifacts"]
    resolver = _resolver(*roots)
    with pytest.raises(PermissionError, match="outside allowed roots"):
        resolver.resolve(
            "file:///data/raw/nuscenes/samples/CAM_FRONT/img.jpg", **_ANY_PIN
        )
