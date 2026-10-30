"""Logical URI grammar (ADR-016): one canonical spelling per object key,
decoded exactly once, never normalized."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.logical_uri import (
    LogicalNamespace,
    LogicalPayloadPin,
    LogicalUri,
    LogicalUriError,
    format_logical_uri,
    parse_logical_uri,
)

SOURCES = LogicalNamespace.SOURCES
PAYLOADS = LogicalNamespace.OBSERVATION_PAYLOADS
CHECKSUM = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    ("namespace", "key", "uri"),
    [
        (
            PAYLOADS,
            "run-001/payload-0123456789abcdef0123456789abcdef",
            "sceneops://observation-payloads/run-001/payload-0123456789abcdef0123456789abcdef",
        ),
        (
            SOURCES,
            "contents/content-0/files/samples/CAM_FRONT/a.jpg",
            "sceneops://sources/contents/content-0/files/samples/CAM_FRONT/a.jpg",
        ),
        (SOURCES, "a+b.jpg", "sceneops://sources/a%2Bb.jpg"),
        (SOURCES, "what?.bin", "sceneops://sources/what%3F.bin"),
        (SOURCES, "frag#1.bin", "sceneops://sources/frag%231.bin"),
        (SOURCES, "100%.bin", "sceneops://sources/100%25.bin"),
        (SOURCES, "a%2Fb", "sceneops://sources/a%252Fb"),
        (SOURCES, "with space/x", "sceneops://sources/with%20space/x"),
        (
            SOURCES,
            "café/日本.pcd",
            "sceneops://sources/caf%C3%A9/%E6%97%A5%E6%9C%AC.pcd",
        ),
        (SOURCES, "~tilde_-.x", "sceneops://sources/~tilde_-.x"),
        (SOURCES, "a:b@c=d,e;f!g$h&i'j(k)l*m", None),
        (SOURCES, "...", "sceneops://sources/..."),
    ],
)
def test_round_trip(namespace, key, uri):
    formatted = format_logical_uri(namespace, key)
    if uri is not None:
        assert formatted == uri
    assert parse_logical_uri(formatted) == LogicalUri(namespace, key)
    # The escaped form uses only unreserved characters, '/' and %XX.
    path = formatted.split("://", 1)[1]
    assert set(path) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/%"
    )


def test_unicode_is_never_normalized():
    composed, decomposed = "café", "café"
    a, b = (format_logical_uri(SOURCES, k) for k in (composed, decomposed))
    assert a != b
    assert parse_logical_uri(a).key == composed
    assert parse_logical_uri(b).key == decomposed


def test_decoding_happens_exactly_once():
    # The key holds a literal '%41'; a second decode would turn it into 'A'.
    uri = format_logical_uri(SOURCES, "x%41")
    assert uri == "sceneops://sources/x%2541"
    assert parse_logical_uri(uri).key == "x%41"


@pytest.mark.parametrize(
    "uri",
    [
        "sceneops://unknown/a",
        "sceneops://Sources/a",
        "SCENEOPS://sources/a",
        "s3://sources/a",
        "sceneops:/sources/a",
        "sceneops://sources",
        "sceneops://sources/",
        "sceneops://observation_payloads/a",
        "sceneops://user@sources/a",
        "sceneops://sources:80/a",
    ],
)
def test_namespace_and_scheme_are_strict(uri):
    with pytest.raises(LogicalUriError):
        parse_logical_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        # traversal, literal and encoded
        "sceneops://sources/../a",
        "sceneops://sources/a/../b",
        "sceneops://sources/./a",
        "sceneops://sources/%2E%2E/a",
        "sceneops://sources/%2e%2e/a",
        "sceneops://sources/a%2F..%2Fb",
        "sceneops://sources/%2E/a",
        # encoded separators and empty segments
        "sceneops://sources/a%2Fb",
        "sceneops://sources/a//b",
        "sceneops://sources//a",
        "sceneops://sources/a/",
        # malformed escapes
        "sceneops://sources/a%",
        "sceneops://sources/a%4",
        "sceneops://sources/a%G1",
        "sceneops://sources/a%%41",
        # non-canonical escapes: lowercase hex, escaped unreserved characters
        "sceneops://sources/a%2bb",
        "sceneops://sources/%41",
        "sceneops://sources/a%7E",
        # invalid UTF-8 and controls
        "sceneops://sources/%FF",
        "sceneops://sources/%C3",
        "sceneops://sources/a%00b",
        "sceneops://sources/a%0Ab",
        "sceneops://sources/a%7Fb",
        "sceneops://sources/a%5Cb",
        # raw characters that must be escaped
        "sceneops://sources/a?b",
        "sceneops://sources/a#b",
        "sceneops://sources/a+b",
        "sceneops://sources/a b",
        "sceneops://sources/café",
        "sceneops://sources/a\\b",
        "sceneops://sources/a?",
        "sceneops://sources/a#",
    ],
)
def test_invalid_or_noncanonical_paths_are_refused(uri):
    with pytest.raises(LogicalUriError):
        parse_logical_uri(uri)


def test_double_encoded_traversal_names_a_literal_key_not_a_parent():
    # '%252E%252E' decodes once to the literal segment '%2E%2E', which is
    # an ordinary name, never '..'.
    parsed = parse_logical_uri("sceneops://sources/%252E%252E/a")
    assert parsed.key == "%2E%2E/a"


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/a",
        "a/",
        "a//b",
        ".",
        "..",
        "a/../b",
        "a/./b",
        "a\\b",
        "a\x00b",
        "a\nb",
        "\ud800",
    ],
)
def test_invalid_keys_have_no_uri(key):
    with pytest.raises(LogicalUriError):
        format_logical_uri(SOURCES, key)


def test_unknown_namespace_has_no_uri():
    with pytest.raises(LogicalUriError):
        LogicalUri("sources", "a")  # type: ignore[arg-type]


def test_pin_requires_a_canonical_uri_and_full_integrity():
    pin = LogicalPayloadPin(
        uri="sceneops://sources/a.bin",
        checksum=CHECKSUM,
        size_bytes=0,
        media_type="application/octet-stream",
    )
    assert pin.size_bytes == 0
    for bad in (
        {"uri": "sceneops://sources/a%2bb"},
        {"uri": "s3://bucket/a.bin"},
        {"checksum": "sha256:ABC"},
        {"checksum": "a" * 64},
        {"size_bytes": -1},
        {"size_bytes": True},
        {"media_type": "Image/JPEG"},
    ):
        fields = {
            "uri": pin.uri,
            "checksum": pin.checksum,
            "size_bytes": pin.size_bytes,
            "media_type": pin.media_type,
            **bad,
        }
        with pytest.raises(LogicalUriError):
            LogicalPayloadPin(**fields)
