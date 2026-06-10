"""Tests for URI normalization helpers."""

from __future__ import annotations

import pytest

from sceneops_worker.inference.detection.uris import (
    local_path_from_uri,
    normalize_image_uri,
    resolve_raw_uri,
)


# ── normalize_image_uri ───────────────────────────────────────────────────────


def test_normalize_absolute_path():
    assert normalize_image_uri("/data/raw/img.jpg") == "file:///data/raw/img.jpg"


def test_normalize_file_uri_unchanged():
    uri = "file:///data/raw/img.jpg"
    assert normalize_image_uri(uri) == uri


def test_normalize_s3_uri_unchanged():
    uri = "s3://bucket/key/img.jpg"
    assert normalize_image_uri(uri) == uri


def test_normalize_relative_path_unchanged():
    assert normalize_image_uri("relative/path.jpg") == "relative/path.jpg"


# ── resolve_raw_uri ───────────────────────────────────────────────────────────


def test_resolve_raw_uri_from_local_root():
    result = resolve_raw_uri("/data/raw/nuscenes", "samples/CAM_FRONT/n.jpg")
    assert result == "file:///data/raw/nuscenes/samples/CAM_FRONT/n.jpg"


def test_resolve_raw_uri_from_file_scheme():
    result = resolve_raw_uri("file:///data/raw/nuscenes", "samples/CAM_FRONT/n.jpg")
    assert result == "file:///data/raw/nuscenes/samples/CAM_FRONT/n.jpg"


def test_resolve_raw_uri_unsupported_scheme():
    with pytest.raises(ValueError, match="unsupported raw_root scheme"):
        resolve_raw_uri("s3://bucket/raw", "samples/CAM_FRONT/n.jpg")


# ── local_path_from_uri ───────────────────────────────────────────────────────


def test_local_path_from_file_uri():
    assert local_path_from_uri("file:///data/raw/img.jpg") == "/data/raw/img.jpg"


def test_local_path_from_bare_path():
    assert local_path_from_uri("/data/raw/img.jpg") == "/data/raw/img.jpg"


def test_local_path_from_s3_uri_raises():
    with pytest.raises(ValueError, match="file://"):
        local_path_from_uri("s3://bucket/img.jpg")
