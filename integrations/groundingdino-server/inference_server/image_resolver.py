"""ImageResolver — loads a PIL image from a URI.

Currently supported:
  file:///absolute/path  → /absolute/path on the local filesystem (shared volume)

Planned (not yet implemented):
  s3://bucket/key        → fetch via boto3
  minio://...            → fetch via minio SDK
  gs://...               → fetch via google-cloud-storage
  https://...            → fetch via requests/httpx

Security: allowed_roots constrains which local paths may be accessed,
preventing path traversal and access to sensitive files.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


class ImageResolver:
    """Resolves an image_uri to a PIL Image.

    Only file:// URIs are supported. The resolved path must fall within one of
    the configured allowed_roots to prevent path traversal attacks.
    """

    def __init__(self, allowed_roots: list[str]) -> None:
        self._allowed_roots = [Path(r).resolve() for r in allowed_roots]

    def resolve(self, image_uri: str):
        """Load and return a PIL Image (RGB) from the given URI.

        Raises:
            ValueError:        URI scheme is not supported (only file://).
            PermissionError:   Resolved path is outside all allowed roots.
            FileNotFoundError: File does not exist at the resolved path.
        """
        from PIL import Image

        path = self._parse_uri(image_uri)
        self._check_allowed(path, image_uri)

        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path} (uri={image_uri!r})")
        return Image.open(path).convert("RGB")

    def _parse_uri(self, image_uri: str) -> Path:
        parsed = urlparse(image_uri)
        if parsed.scheme == "file":
            return Path(parsed.path).resolve()
        raise ValueError(
            f"Unsupported image URI scheme: {parsed.scheme!r} in {image_uri!r}. "
            "Supported: file://. "
            "TODO: add s3://, minio://, gs://, https:// via ImageResolver extensions."
        )

    def _check_allowed(self, path: Path, image_uri: str) -> None:
        for root in self._allowed_roots:
            if path.is_relative_to(root):
                return
        raise PermissionError(
            f"Image path {path} is outside allowed roots "
            f"(uri={image_uri!r}, allowed={[str(r) for r in self._allowed_roots]})"
        )
