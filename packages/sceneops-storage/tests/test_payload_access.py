"""PayloadUriResolver and VerifiedPayloadReader (ADR-016) over the local
store -- filesystem-only; the S3 equivalents are in test_s3_payload_access.py."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.logical_uri import (
    LogicalNamespace,
    LogicalPayloadPin,
    format_logical_uri,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.config import ArtifactSettings
from sceneops_storage import (
    LocalArtifactStore,
    PayloadMissingError,
    PayloadUriResolver,
    PayloadVerificationError,
    UnresolvablePayloadUriError,
    VerifiedPayloadReader,
)

PAYLOADS = LogicalNamespace.OBSERVATION_PAYLOADS
SOURCES = LogicalNamespace.SOURCES
KEYS = [
    "run-001/payload-0123456789abcdef0123456789abcdef",
    "contents/content-0/files/a+b?c#d%e/é 1.pcd.bin",
]


def _resolver(root: str) -> PayloadUriResolver:
    return PayloadUriResolver(
        {PAYLOADS: f"{root}/observation_payloads", SOURCES: f"{root}/sources"}
    )


def _pin(uri: str, data: bytes, **overrides) -> LogicalPayloadPin:
    fields = {
        "uri": uri,
        "checksum": sha256_checksum(data),
        "size_bytes": len(data),
        "media_type": "application/octet-stream",
        **overrides,
    }
    return LogicalPayloadPin(**fields)


@pytest.fixture()
def store(tmp_path):
    return LocalArtifactStore(root_uri=str(tmp_path))


# ── resolution ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize(
    "root", ["/data/artifacts", "file:///data/artifacts", "s3://bucket-a/artifacts"]
)
def test_resolution_appends_the_key_verbatim_and_reverses(root, key):
    resolver = _resolver(root)
    for namespace, prefix in ((PAYLOADS, "observation_payloads"), (SOURCES, "sources")):
        uri = format_logical_uri(namespace, key)
        physical = resolver.resolve(uri)
        assert physical == f"{root}/{prefix}/{key}"
        assert resolver.logical_uri(physical) == uri


def test_the_logical_uri_does_not_depend_on_bucket_or_endpoint():
    key = KEYS[1]
    deployments = [
        _resolver("s3://bucket-a/artifacts"),
        _resolver("s3://bucket-b/elsewhere/artifacts"),
        _resolver("/data/artifacts"),
    ]
    uris = {
        d.logical_uri(d.resolve(format_logical_uri(SOURCES, key))) for d in deployments
    }
    assert uris == {format_logical_uri(SOURCES, key)}
    physical = {d.resolve(format_logical_uri(SOURCES, key)) for d in deployments}
    assert len(physical) == 3


def test_from_settings_uses_the_configured_producer_roots():
    settings = ArtifactSettings(root_uri="s3://sceneops/artifacts")
    resolver = PayloadUriResolver.from_settings(settings)
    assert resolver.roots == {
        PAYLOADS: settings.observation_payload_root_uri,
        SOURCES: settings.source_root_uri,
    }
    assert (
        resolver.resolve("sceneops://sources/contents/c/files/x")
        == "s3://sceneops/artifacts/sources/contents/c/files/x"
    )


@pytest.mark.parametrize(
    "uri",
    [
        "sceneops://unknown/x",
        "sceneops://sources/../x",
        "sceneops://sources/%2E%2E/x",
        "sceneops://sources/a%2Fb",
        "sceneops://sources/a%2bb",
        "s3://bucket/artifacts/sources/x",
        "/data/artifacts/sources/x",
    ],
)
def test_resolution_refuses_anything_but_a_canonical_logical_uri(uri):
    with pytest.raises(UnresolvablePayloadUriError):
        _resolver("/data/artifacts").resolve(uri)


def test_an_unconfigured_namespace_is_refused():
    resolver = PayloadUriResolver({SOURCES: "/data/artifacts/sources"})
    with pytest.raises(UnresolvablePayloadUriError, match="no configured root"):
        resolver.resolve("sceneops://observation-payloads/run/p")


@pytest.mark.parametrize(
    "physical",
    [
        # a sibling whose name merely starts with the root
        "/data/artifacts/sources-old/contents/x",
        "/data/artifacts/sourcesX",
        # the root itself, or a non-key below it
        "/data/artifacts/sources",
        "/data/artifacts/sources/",
        "/data/artifacts/sources//x",
        "/data/artifacts/sources/a/../b",
        "/data/artifacts/sources/./b",
        "/data/artifacts/sources/a\\b",
        # another root spelling
        "file:///data/artifacts/sources/x",
        "/data/artifacts/canonical/x",
    ],
)
def test_reverse_mapping_is_strict(physical):
    with pytest.raises(UnresolvablePayloadUriError):
        _resolver("/data/artifacts").logical_uri(physical)


@pytest.mark.parametrize(
    "roots",
    [
        {PAYLOADS: "/data/a", SOURCES: "/data/a"},
        {PAYLOADS: "/data/a", SOURCES: "/data/a/sources"},
        {PAYLOADS: "/data/a/payloads", SOURCES: "/data/a"},
        {PAYLOADS: "/data/a/", SOURCES: "/data/b"},
        {PAYLOADS: "", SOURCES: "/data/b"},
        {"sources": "/data/b"},
    ],
)
def test_ambiguous_or_malformed_roots_are_refused(roots):
    with pytest.raises(ValueError):
        PayloadUriResolver(roots)


def test_roots_that_share_a_name_prefix_are_not_ambiguous():
    resolver = PayloadUriResolver({PAYLOADS: "/data/src", SOURCES: "/data/sources"})
    assert resolver.logical_uri("/data/sources/x") == "sceneops://sources/x"
    assert resolver.logical_uri("/data/src/x") == "sceneops://observation-payloads/x"


# ── verified reads ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", KEYS)
async def test_read_returns_exactly_the_pinned_bytes(store, tmp_path, key):
    resolver = _resolver(str(tmp_path))
    uri = format_logical_uri(SOURCES, key)
    data = b"\x00point cloud\xff"
    await store.write_bytes(resolver.resolve(uri), data)
    assert await VerifiedPayloadReader(store, resolver).read(_pin(uri, data)) == data


async def test_an_empty_object_is_valid_when_pinned_empty(store, tmp_path):
    resolver = _resolver(str(tmp_path))
    uri = format_logical_uri(SOURCES, "contents/c/files/empty")
    await store.write_bytes(resolver.resolve(uri), b"")
    assert await VerifiedPayloadReader(store, resolver).read(_pin(uri, b"")) == b""


async def test_a_missing_object_is_reported_as_missing(store, tmp_path):
    resolver = _resolver(str(tmp_path))
    uri = format_logical_uri(PAYLOADS, "run-001/payload-x")
    with pytest.raises(PayloadMissingError, match="run-001/payload-x"):
        await VerifiedPayloadReader(store, resolver).read(_pin(uri, b"abc"))


async def test_a_size_mismatch_is_an_integrity_violation(store, tmp_path):
    resolver = _resolver(str(tmp_path))
    uri = format_logical_uri(PAYLOADS, "run-001/payload-x")
    await store.write_bytes(resolver.resolve(uri), b"abcd")
    with pytest.raises(PayloadVerificationError, match="has 4 bytes"):
        await VerifiedPayloadReader(store, resolver).read(_pin(uri, b"abc"))


async def test_a_checksum_mismatch_is_an_integrity_violation(store, tmp_path):
    resolver = _resolver(str(tmp_path))
    uri = format_logical_uri(PAYLOADS, "run-001/payload-x")
    await store.write_bytes(resolver.resolve(uri), b"abc")
    pin = _pin(uri, b"abc", checksum=sha256_checksum(b"xyz"))
    with pytest.raises(PayloadVerificationError, match="the pin requires"):
        await VerifiedPayloadReader(store, resolver).read(pin)


async def test_corrupt_bytes_of_unchanged_size_are_refused(store, tmp_path):
    resolver = _resolver(str(tmp_path))
    uri = format_logical_uri(PAYLOADS, "run-001/payload-x")
    data = bytes(range(256))
    pin = _pin(uri, data)
    corrupt = bytearray(data)
    corrupt[100] ^= 0x01
    await store.write_bytes(resolver.resolve(uri), bytes(corrupt))
    with pytest.raises(PayloadVerificationError):
        await VerifiedPayloadReader(store, resolver).read(pin)


async def test_an_unresolvable_pin_reads_nothing(store, tmp_path):
    resolver = PayloadUriResolver({SOURCES: f"{tmp_path}/sources"})
    pin = _pin(format_logical_uri(PAYLOADS, "run/p"), b"x")
    with pytest.raises(UnresolvablePayloadUriError):
        await VerifiedPayloadReader(store, resolver).read(pin)
