"""URI helpers for detection inference image location.

All image locations flow through these functions so the contract between
the worker adapter and the inference server stays consistent.

Current support: file:// (local filesystem / shared volume)
Future:          s3://, minio://, gs://  via an ImageResolver abstraction
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def normalize_image_uri(uri_or_path: str) -> str:
    """Ensure a local absolute path is represented as a file:// URI.

    Existing scheme-prefixed URIs (file://, s3://, etc.) are returned unchanged.
    """
    if "://" in uri_or_path:
        return uri_or_path
    if uri_or_path.startswith("/"):
        return f"file://{uri_or_path}"
    return uri_or_path


def resolve_raw_uri(raw_root: str, relative_path: str) -> str:
    """Build a file:// image URI from a dataset raw_root and a sensor-relative path.

    raw_root examples:
      "/data/raw/nuscenes"           → local absolute path
      "file:///data/raw/nuscenes"    → file URI (with or without trailing slash)

    relative_path: "samples/CAM_FRONT/n008-xxx.jpg"

    Returns: "file:///data/raw/nuscenes/samples/CAM_FRONT/n008-xxx.jpg"
    """
    parsed = urlparse(raw_root)
    if parsed.scheme == "file":
        base = Path(parsed.path)
    elif parsed.scheme == "":
        base = Path(raw_root)
    else:
        raise ValueError(
            f"resolve_raw_uri: unsupported raw_root scheme {parsed.scheme!r}. "
            f"Got: {raw_root!r}. "
            "TODO: add s3://, minio://, gs:// support via ImageResolver."
        )
    return f"file://{base / relative_path}"


def local_path_from_uri(image_uri: str) -> str:
    """Extract the local filesystem path from a file:// URI.

    Raises ValueError for non-file:// URIs so callers that can only handle
    local files get a clear error instead of a silent wrong path.
    """
    parsed = urlparse(image_uri)
    if parsed.scheme == "file":
        return parsed.path
    if parsed.scheme == "":
        # Bare path — treat as local for backward compat.
        return image_uri
    raise ValueError(
        f"local_path_from_uri: expected file:// URI, "
        f"got scheme={parsed.scheme!r} in {image_uri!r}. "
        "TODO: support remote image resolvers."
    )
