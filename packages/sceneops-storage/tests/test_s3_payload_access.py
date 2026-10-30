"""Verified payload reads against real MinIO (ADR-016): the S3 backend keeps
an object key containing '+', '?', '#', '%' and non-ASCII characters exactly,
so a Logical URI resolves to the object its producer wrote, and the reader
verifies the bytes it gets back."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.logical_uri import (
    LogicalNamespace,
    LogicalPayloadPin,
    format_logical_uri,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_storage import (
    PayloadMissingError,
    PayloadUriResolver,
    PayloadVerificationError,
    VerifiedPayloadReader,
)

SOURCES = LogicalNamespace.SOURCES
KEY = "contents/content-0/files/a+b?c#d%25e/é 1.pcd.bin"


def _setup(bucket: str, unique_key):
    root = f"s3://{bucket}/{unique_key()}"
    resolver = PayloadUriResolver(
        {
            LogicalNamespace.OBSERVATION_PAYLOADS: f"{root}/observation_payloads",
            SOURCES: f"{root}/sources",
        }
    )
    return root, resolver


def _pin(uri: str, data: bytes) -> LogicalPayloadPin:
    return LogicalPayloadPin(
        uri=uri,
        checksum=sha256_checksum(data),
        size_bytes=len(data),
        media_type="application/octet-stream",
    )


@pytest.mark.asyncio
async def test_s3_keys_with_special_characters_resolve_and_verify(
    store, bucket, unique_key
):
    root, resolver = _setup(bucket, unique_key)
    uri = format_logical_uri(SOURCES, KEY)
    physical = resolver.resolve(uri)
    assert physical == f"{root}/sources/{KEY}"
    data = b"lidar bytes \x00\xff"
    await store.write_bytes(physical, data)
    # The object is listed under exactly the key the resolver produced.
    listed = [o.uri for o in await store.list_objects(f"{root}/sources")]
    assert listed == [physical]
    assert resolver.logical_uri(listed[0]) == uri
    assert await VerifiedPayloadReader(store, resolver).read(_pin(uri, data)) == data


@pytest.mark.asyncio
async def test_s3_missing_and_corrupt_objects_are_refused(store, bucket, unique_key):
    _, resolver = _setup(bucket, unique_key)
    reader = VerifiedPayloadReader(store, resolver)
    uri = format_logical_uri(SOURCES, KEY)
    data = bytes(range(64))
    with pytest.raises(PayloadMissingError):
        await reader.read(_pin(uri, data))
    corrupt = bytearray(data)
    corrupt[7] ^= 0x80
    await store.write_bytes(resolver.resolve(uri), bytes(corrupt))
    with pytest.raises(PayloadVerificationError):
        await reader.read(_pin(uri, data))


@pytest.mark.asyncio
async def test_s3_empty_object_is_valid(store, bucket, unique_key):
    _, resolver = _setup(bucket, unique_key)
    uri = format_logical_uri(SOURCES, "contents/content-0/files/empty")
    await store.write_bytes(resolver.resolve(uri), b"")
    assert await VerifiedPayloadReader(store, resolver).read(_pin(uri, b"")) == b""
