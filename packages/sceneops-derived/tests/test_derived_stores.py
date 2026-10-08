"""Write-once, checksum-pinned storage of derived manifests and run artifacts, on a local
ArtifactStore (no infrastructure)."""

from __future__ import annotations

import pytest

from sceneops_core.common.checksums import sha256_checksum
from sceneops_derived import (
    DerivedManifestConflictError,
    DerivedManifestIntegrityError,
    DerivedManifestStore,
    RunArtifactStore,
)
from sceneops_storage import LocalArtifactStore


@pytest.fixture()
def store(tmp_path) -> DerivedManifestStore:
    root = str(tmp_path)
    return DerivedManifestStore(
        artifact_store=LocalArtifactStore(root_uri=root),
        dataset_root_uri=f"{root}/datasets",
        runs_root_uri=f"{root}/runs",
        label_root_uri=f"{root}/labels",
    )


def test_keys_are_qualified_by_the_manifest_checksum(
    store: DerivedManifestStore,
) -> None:
    first = store.label_set_uri(label_set_id="labels-1", checksum=sha256_checksum(b"a"))
    second = store.label_set_uri(
        label_set_id="labels-1", checksum=sha256_checksum(b"b")
    )

    assert first != second
    assert first.endswith(".json") and "/labels-1/manifest-" in first


async def test_publishing_the_same_bytes_again_is_a_no_op(
    store: DerivedManifestStore,
) -> None:
    data = b'{"revision": 1}'
    uri = store.label_set_uri(label_set_id="labels-1", checksum=sha256_checksum(data))

    first = await store.publish(uri=uri, data=data)
    second = await store.publish(uri=uri, data=data)

    assert first.created is True
    assert second.created is False


async def test_different_bytes_under_an_existing_key_are_a_conflict(
    store: DerivedManifestStore,
) -> None:
    uri = store.label_set_uri(label_set_id="labels-1", checksum=sha256_checksum(b"a"))
    await store.publish(uri=uri, data=b"a")

    with pytest.raises(DerivedManifestConflictError):
        await store.publish(uri=uri, data=b"not a")

    assert await store.read_pinned(uri=uri, checksum=sha256_checksum(b"a")) == b"a"


async def test_a_pinned_read_verifies_checksum_and_size(
    store: DerivedManifestStore,
) -> None:
    data = b'{"revision": 2}'
    checksum = sha256_checksum(data)
    uri = store.label_set_uri(label_set_id="labels-2", checksum=checksum)
    await store.publish(uri=uri, data=data)

    assert (
        await store.read_pinned(uri=uri, checksum=checksum, size_bytes=len(data))
        == data
    )
    with pytest.raises(DerivedManifestIntegrityError):
        await store.read_pinned(uri=uri, checksum=sha256_checksum(b"other"))
    with pytest.raises(DerivedManifestIntegrityError):
        await store.read_pinned(uri=uri, checksum=checksum, size_bytes=len(data) + 1)


async def test_a_missing_manifest_is_an_integrity_error(
    store: DerivedManifestStore,
) -> None:
    uri = store.label_set_uri(label_set_id="absent", checksum=sha256_checksum(b"x"))

    with pytest.raises(DerivedManifestIntegrityError):
        await store.read_pinned(uri=uri, checksum=sha256_checksum(b"x"))


def test_run_artifacts_live_under_their_run(tmp_path) -> None:
    runs = RunArtifactStore(
        artifact_store=LocalArtifactStore(root_uri=str(tmp_path)),
        runs_root_uri=f"{tmp_path}/runs",
    )

    assert runs.inference_run_root_uri("inf-1").endswith("/runs/inference/inf-1")
    assert runs.evaluation_run_root_uri("eval-1").endswith("/eval-1")
