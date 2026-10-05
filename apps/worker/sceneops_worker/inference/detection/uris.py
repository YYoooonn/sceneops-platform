"""URI helpers for detection inference image location.

All image locations flow through these functions so the contract between
the worker adapter and the inference server stays consistent.

Current support: file:// (local filesystem / shared volume)
Future:          s3://, minio://, gs://  via an ImageResolver abstraction
"""

from __future__ import annotations


def normalize_image_uri(uri_or_path: str) -> str:
    """Ensure a local absolute path is represented as a file:// URI.

    Existing scheme-prefixed URIs (file://, s3://, etc.) are returned unchanged.
    """
    if "://" in uri_or_path:
        return uri_or_path
    if uri_or_path.startswith("/"):
        return f"file://{uri_or_path}"
    return uri_or_path
