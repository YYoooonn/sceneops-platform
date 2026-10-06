"""Integration coverage for S3ArtifactStore against real MinIO.

Verifies the real backend honors the same ArtifactStore contract already
unit-tested against LocalArtifactStore (apps/worker/tests/datasets/
test_observation_artifact_store.py) — not a re-test of that logic, just
confirmation the real S3-compatible client behaves the same way.
"""

from __future__ import annotations

import asyncio

import pytest


# ── write / read / exists ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_then_read_json_round_trips(store, bucket, unique_key):
    artifact_store = store
    key = unique_key("round-trip.json")
    uri = f"s3://{bucket}/{key}"

    await artifact_store.write_json(uri, {"hello": "world", "count": 3})
    result = await artifact_store.read_json(uri)

    assert result == {"hello": "world", "count": 3}


@pytest.mark.asyncio
async def test_exists_false_before_write_true_after(store, bucket, unique_key):
    artifact_store = store
    key = unique_key("exists-check.json")
    uri = f"s3://{bucket}/{key}"

    assert await artifact_store.exists(uri) is False
    await artifact_store.write_json(uri, {"x": 1})
    assert await artifact_store.exists(uri) is True


@pytest.mark.asyncio
async def test_read_missing_object_raises_not_found(store, bucket, unique_key):
    from sceneops_storage.exceptions import ArtifactNotFoundError

    artifact_store = store
    key = unique_key("never-written.json")
    uri = f"s3://{bucket}/{key}"

    with pytest.raises(ArtifactNotFoundError):
        await artifact_store.read_json(uri)


@pytest.mark.asyncio
async def test_write_then_read_bytes_round_trips(store, bucket, unique_key):
    artifact_store = store
    key = unique_key("blob.bin")
    uri = f"s3://{bucket}/{key}"

    payload = b"\x00\x01binary-payload\xff"
    await artifact_store.write_bytes(uri, payload)
    result = await artifact_store.read_bytes(uri)

    assert result == payload


# ── read_range (SceneOps V2 Request 5.3) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_read_range_returns_exact_slice(store, bucket, unique_key):
    artifact_store = store
    key = unique_key("range.bin")
    uri = f"s3://{bucket}/{key}"

    await artifact_store.write_bytes(uri, b"0123456789abcdef")
    result = await artifact_store.read_range(uri, 3, 5)

    assert result == b"34567"


@pytest.mark.asyncio
async def test_read_range_missing_object_raises_not_found(store, bucket, unique_key):
    from sceneops_storage.exceptions import ArtifactNotFoundError

    artifact_store = store
    key = unique_key("never-written.bin")
    uri = f"s3://{bucket}/{key}"

    with pytest.raises(ArtifactNotFoundError):
        await artifact_store.read_range(uri, 0, 4)


@pytest.mark.asyncio
async def test_read_range_negative_offset_raises(store, bucket, unique_key):
    from sceneops_storage.exceptions import ArtifactReadError

    artifact_store = store
    key = unique_key("range-invalid.bin")
    uri = f"s3://{bucket}/{key}"
    await artifact_store.write_bytes(uri, b"0123456789")

    with pytest.raises(ArtifactReadError):
        await artifact_store.read_range(uri, -1, 4)


@pytest.mark.asyncio
async def test_read_range_past_end_of_object_raises(store, bucket, unique_key):
    from sceneops_storage.exceptions import ArtifactReadError

    artifact_store = store
    key = unique_key("range-past-end.bin")
    uri = f"s3://{bucket}/{key}"
    await artifact_store.write_bytes(uri, b"0123456789")

    with pytest.raises(ArtifactReadError):
        await artifact_store.read_range(uri, 8, 100)


# ── list ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_json_returns_written_objects_under_prefix(
    store, bucket, unique_key
):
    artifact_store = store
    prefix = unique_key("listing")

    await artifact_store.write_json(f"s3://{bucket}/{prefix}/a.json", {"n": "a"})
    await artifact_store.write_json(f"s3://{bucket}/{prefix}/b.json", {"n": "b"})

    result = await artifact_store.list_json(f"s3://{bucket}/{prefix}")

    assert result == sorted(
        [f"s3://{bucket}/{prefix}/a.json", f"s3://{bucket}/{prefix}/b.json"]
    )


@pytest.mark.asyncio
async def test_list_json_empty_prefix_returns_empty_list(store, bucket, unique_key):
    artifact_store = store
    prefix = unique_key("empty-listing")

    result = await artifact_store.list_json(f"s3://{bucket}/{prefix}")
    assert result == []


@pytest.mark.asyncio
async def test_list_objects_is_recursive_across_pages_with_size_and_mtime(
    store, bucket, unique_key
):
    from datetime import UTC, datetime, timedelta

    artifact_store = store
    prefix = unique_key("list-objects")

    # More than one list_objects_v2 page (1000 keys per page), nested.
    semaphore = asyncio.Semaphore(32)

    async def _put(index: int) -> None:
        async with semaphore:
            await artifact_store.write_bytes(
                f"s3://{bucket}/{prefix}/run-{index % 7}/obj-{index:04d}.bin",
                b"x" * (index % 5 + 1),
            )

    await asyncio.gather(*(_put(i) for i in range(1005)))
    # A sibling whose name merely starts with the prefix must not be listed.
    await artifact_store.write_bytes(f"s3://{bucket}/{prefix}-sibling/y.bin", b"y")

    objects = await artifact_store.list_objects(f"s3://{bucket}/{prefix}")

    assert len(objects) == 1005
    uris = [o.uri for o in objects]
    assert uris == sorted(uris)
    assert all(u.startswith(f"s3://{bucket}/{prefix}/") for u in uris)
    by_uri = {o.uri: o for o in objects}
    sample = by_uri[f"s3://{bucket}/{prefix}/run-3/obj-0003.bin"]
    assert sample.size_bytes == 3 % 5 + 1
    assert sample.last_modified.tzinfo is not None
    assert abs(datetime.now(UTC) - sample.last_modified) < timedelta(minutes=10)


@pytest.mark.asyncio
async def test_list_objects_empty_prefix_returns_empty_list(store, bucket, unique_key):
    artifact_store = store
    prefix = unique_key("empty-list-objects")

    assert await artifact_store.list_objects(f"s3://{bucket}/{prefix}") == []


# ── URI / path resolution ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_join_uri_builds_expected_key_structure(store, bucket):
    artifact_store = store
    joined = artifact_store.join_uri(
        f"s3://{bucket}/datasets/ds1/versions/v1", "raw", "log.json"
    )
    assert joined == f"s3://{bucket}/datasets/ds1/versions/v1/raw/log.json"


@pytest.mark.asyncio
async def test_unsupported_uri_scheme_raises(store):
    artifact_store = store
    with pytest.raises(ValueError, match="Unsupported S3 artifact URI scheme"):
        await artifact_store.exists("file:///not/an/s3/uri.json")


# ── overwrite behavior ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_json_overwrites_same_uri(store, bucket, unique_key):
    """Writing the SAME uri twice is an intentional overwrite — this is the
    behavior F-01's raw_log_id scoping exists to avoid triggering
    accidentally (see test_raw_log_isolation_same_dataset_version below)."""
    artifact_store = store
    key = unique_key("overwrite.json")
    uri = f"s3://{bucket}/{key}"

    await artifact_store.write_json(uri, {"version": 1})
    await artifact_store.write_json(uri, {"version": 2})

    result = await artifact_store.read_json(uri)
    assert result == {"version": 2}


# ── F-01 invariant: raw_log_id isolation against the real backend ────────────


@pytest.mark.asyncio
async def test_raw_log_isolation_same_dataset_version(store, bucket, unique_key):
    """SceneOps V2 Stabilization Request 2 (F-01): raw-log-derived artifacts
    for two different raw_log_ids under the SAME dataset_id/dataset_version
    must resolve to distinct objects, and writing the second must not
    overwrite the first — verified here against the real MinIO backend
    (apps/worker's ObservationArtifactStore is unit-tested against
    LocalArtifactStore already; this confirms the same contract holds for
    the real S3-compatible client, not the abstraction alone)."""
    artifact_store = store
    version_root = unique_key("datasets/nuscenes-it/versions/v1")

    def raw_log_manifest_uri(raw_log_id: str) -> str:
        return artifact_store.join_uri(
            f"s3://{bucket}/{version_root}", "raw", raw_log_id, "raw_log.json"
        )

    uri_a = raw_log_manifest_uri("raw-log-A")
    uri_b = raw_log_manifest_uri("raw-log-B")
    assert uri_a != uri_b

    await artifact_store.write_json(uri_a, {"rawLogId": "raw-log-A"})
    await artifact_store.write_json(uri_b, {"rawLogId": "raw-log-B"})

    # Writing B must not have touched A.
    result_a = await artifact_store.read_json(uri_a)
    result_b = await artifact_store.read_json(uri_b)
    assert result_a == {"rawLogId": "raw-log-A"}
    assert result_b == {"rawLogId": "raw-log-B"}
