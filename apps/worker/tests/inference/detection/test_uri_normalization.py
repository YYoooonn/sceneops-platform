"""Tests for URI normalization helpers."""

from __future__ import annotations

from sceneops_worker.inference.detection.uris import normalize_image_uri


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
