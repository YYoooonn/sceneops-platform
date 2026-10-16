"""ImageResolver — loads a PIL image from a URI.

Supported:
  file:///absolute/path  → /absolute/path on the local filesystem (shared volume)
  s3://bucket/key        → GetObject through the configured S3 client (MinIO included)

Every other scheme is rejected.

Security: allowed_roots constrains which local paths may be accessed,
preventing path traversal and access to sensitive files.
"""

from __future__ import annotations

from pathlib import Path
from io import BytesIO
from urllib.parse import ParseResult, urlparse

from botocore.exceptions import ClientError
from PIL import Image


class ImageResolver:
    """Resolves an image_uri to a PIL Image.

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

    def resolve(self, image_uri: str):
        """Load and return a PIL Image (RGB) from the given URI.

        Raises:
            ValueError:        URI scheme is not file:// or s3://, or an s3:// URI
                               has no bucket or key, or the object is not an image.
            RuntimeError:      s3:// URI but no S3 client is configured.
            PermissionError:   Resolved path is outside all allowed roots.
            FileNotFoundError: File or S3 object does not exist.
        """
        parsed = urlparse(image_uri)

        if parsed.scheme == "file":
            return self._resolve_file(parsed, image_uri)

        if parsed.scheme == "s3":
            return self._resolve_s3(parsed, image_uri)
        raise ValueError(
            f"Unsupported image URI scheme: {parsed.scheme!r} "
            f"in {image_uri!r}. Supported: file://, s3://."
        )

    def _resolve_file(
        self,
        parsed: ParseResult,
        image_uri: str,
    ) -> Image.Image:
        path = Path(parsed.path).resolve()

        self._check_allowed(path, image_uri)

        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path} (uri={image_uri!r})")

        with Image.open(path) as image:
            return image.convert("RGB")

    def _resolve_s3(
        self,
        parsed: ParseResult,
        image_uri: str,
    ) -> Image.Image:
        if self._s3_client is None:
            raise RuntimeError("S3 image resolution is not configured")

        bucket = parsed.netloc
        key = parsed.path.lstrip("/")

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

        payload = response["Body"].read()

        try:
            with Image.open(BytesIO(payload)) as image:
                return image.convert("RGB")
        except Exception as exc:
            raise ValueError(f"S3 object is not a valid image: {image_uri!r}") from exc

    def _check_allowed(self, path: Path, image_uri: str) -> None:
        for root in self._allowed_roots:
            if path.is_relative_to(root):
                return
        raise PermissionError(
            f"Image path {path} is outside allowed roots "
            f"(uri={image_uri!r}, allowed={[str(r) for r in self._allowed_roots]})"
        )
