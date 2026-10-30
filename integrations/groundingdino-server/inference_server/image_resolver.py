"""ImageResolver — loads a PIL image from a URI, only from verified bytes.

Supported:
  file:///absolute/path  → /absolute/path on the local filesystem (shared volume)
  s3://bucket/key        → GetObject through the configured S3 client (MinIO included)

Every other scheme is rejected.

Every image is pinned by its stored object's exact byte count and full sha256.
The resolver reads the object once, checks both, and decodes those same bytes:
the location is never reopened after the check, so the bytes the model sees are
the bytes that were verified. There is no unpinned read.

The path or key is everything after the authority, verbatim (no percent-decoding;
``?`` and ``#`` are ordinary characters), as the SceneOps storage backends that
wrote the object spell it.

Security: allowed_roots constrains which local paths may be accessed,
preventing path traversal and access to sensitive files.
"""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from botocore.exceptions import ClientError
from PIL import Image

# "sha256:" + 64 lowercase hex: the SceneOps checksum spelling.
IMAGE_CHECKSUM_PATTERN = r"^sha256:[0-9a-f]{64}$"
_CHECKSUM_RE = re.compile(IMAGE_CHECKSUM_PATTERN)
_READ_CHUNK = 1 << 20


class ImageIntegrityError(Exception):
    """The stored object's size or sha256 differs from the request's pin."""


class ImageDecodeError(ValueError):
    """Verified bytes are not a decodable image."""


class ImageResolver:
    """Resolves a pinned image_uri to a PIL Image.

    file:// and s3:// URIs are supported. A file:// path must fall within one of
    the configured allowed_roots to prevent path traversal attacks.
    """

    def __init__(
        self,
        *,
        allowed_roots: list[str],
        s3_client=None,
    ) -> None:
        self._allowed_roots = [Path(root).resolve() for root in allowed_roots]
        self._s3_client = s3_client

    def resolve(self, image_uri: str, *, checksum: str, size_bytes: int):
        """Load and return a PIL Image (RGB) decoded from the verified bytes of
        the object at ``image_uri``.

        Raises:
            ValueError:          URI scheme is not file:// or s3://, an s3:// URI
                                 has no bucket or key, or the pin is malformed.
            RuntimeError:        s3:// URI but no S3 client is configured.
            PermissionError:     Resolved path is outside all allowed roots.
            FileNotFoundError:   File or S3 object does not exist.
            ImageIntegrityError: Stored size or sha256 differs from the pin.
            ImageDecodeError:    Verified bytes are not an image.
        """
        data = self.read_verified(image_uri, checksum=checksum, size_bytes=size_bytes)
        return decode_image(data, image_uri)

    def read_verified(self, image_uri: str, *, checksum: str, size_bytes: int) -> bytes:
        """The whole object at ``image_uri``, only if it is exactly the pinned
        bytes."""
        if not isinstance(checksum, str) or not _CHECKSUM_RE.fullmatch(checksum):
            raise ValueError(
                f"checksum must be 'sha256:<64 lowercase hex>', got {checksum!r}"
            )
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise ValueError(
                f"size_bytes must be a non-negative int, got {size_bytes!r}"
            )

        scheme = urlparse(image_uri).scheme
        if scheme == "file":
            data = self._read_file(image_uri, limit=size_bytes)
        elif scheme == "s3":
            data = self._read_s3(image_uri, limit=size_bytes)
        else:
            raise ValueError(
                f"Unsupported image URI scheme: {scheme!r} "
                f"in {image_uri!r}. Supported: file://, s3://."
            )

        if len(data) != size_bytes:
            # A longer object is read only one byte past the pin.
            found = (
                f"more than {size_bytes}" if len(data) > size_bytes else str(len(data))
            )
            raise ImageIntegrityError(
                f"image {image_uri!r} has {found} bytes; the pin requires {size_bytes}"
            )
        actual = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if actual != checksum:
            raise ImageIntegrityError(
                f"image {image_uri!r} has {actual}; the pin requires {checksum}"
            )
        return data

    def _read_file(self, image_uri: str, *, limit: int) -> bytes:
        path = Path(_after_authority(image_uri)).resolve()

        self._check_allowed(path, image_uri)

        try:
            with path.open("rb") as stream:
                return _read_bounded(stream, limit)
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
            raise FileNotFoundError(
                f"Image not found: {path} (uri={image_uri!r})"
            ) from exc

    def _read_s3(self, image_uri: str, *, limit: int) -> bytes:
        if self._s3_client is None:
            raise RuntimeError("S3 image resolution is not configured")

        bucket = urlparse(image_uri).netloc
        key = _after_authority(image_uri).lstrip("/")

        if not bucket or not key:
            raise ValueError(f"Invalid S3 image URI: {image_uri!r}")

        try:
            response = self._s3_client.get_object(
                Bucket=bucket,
                Key=key,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")

            if code in {"NoSuchKey", "NoSuchBucket", "404"}:
                raise FileNotFoundError(f"S3 image not found: {image_uri!r}") from exc

            raise

        body = response["Body"]
        try:
            return _read_bounded(body, limit)
        finally:
            body.close()

    def _check_allowed(self, path: Path, image_uri: str) -> None:
        for root in self._allowed_roots:
            if path.is_relative_to(root):
                return
        raise PermissionError(
            f"Image path {path} is outside allowed roots "
            f"(uri={image_uri!r}, allowed={[str(r) for r in self._allowed_roots]})"
        )


def decode_image(data: bytes, image_uri: str) -> Image.Image:
    """Decode image bytes to RGB, fully, from memory."""
    try:
        with Image.open(BytesIO(data)) as image:
            return image.convert("RGB")
    except Exception as exc:
        raise ImageDecodeError(
            f"image {image_uri!r} is not a valid image: {exc}"
        ) from exc


def _after_authority(uri: str) -> str:
    # urlparse's path stops at '?' and '#', which are ordinary characters in a
    # stored file name or object key, so the path is taken verbatim.
    return uri.split("://", 1)[1][len(urlparse(uri).netloc) :]


def _read_bounded(stream: BinaryIO, limit: int) -> bytes:
    """At most ``limit + 1`` bytes: enough to tell an exact-size object from a
    longer one without reading all of a longer one."""
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        chunk = stream.read(min(remaining, _READ_CHUNK))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)
