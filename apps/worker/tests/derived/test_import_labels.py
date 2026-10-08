"""IMPORT_LABELS: label documents become immutable, registered, pinned
revisions with their own lineage (ADR-007 §33.2)."""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.jobs.schemas import ImportLabelsJobParams, JobType
from sceneops_worker.jobs.derived import ImportLabelsJobHandler
from sceneops_worker.jobs.derived.import_labels import (
    LabelDocumentChecksumError,
    UnknownRobotRunError,
)
from sceneops_derived.testing import label_document, write_document
from tests.derived_harness import make_harness

T0, T1 = 1_000_000_000, 1_500_000_000


async def _import(harness, uri, **params):
    request = harness.request(
        JobType.IMPORT_LABELS, ImportLabelsJobParams(document_uri=uri, **params)
    )
    return await ImportLabelsJobHandler().run(request)


async def _harness_with_run(tmp_path, run="run-001"):
    harness = make_harness(tmp_path)
    harness.robot_store.run_ids.add(run)
    return harness


async def test_import_registers_one_pinned_revision(tmp_path) -> None:
    harness = await _harness_with_run(tmp_path)
    doc = label_document("gt", covered=[T0, T1], labels={"a": (T0, 10.0)})
    result = await _import(harness, await write_document(harness, "gt.json", doc))

    assert result.created is True
    assert result.label_count == 1 and result.covered_anchor_count == 2
    assert result.robot_run_ids == ["run-001"]
    assert result.provenance_kind == "external"
    assert result.label_set.manifest_checksum == doc.checksum()

    record = await harness.artifact_record_store.get(
        result.label_set.manifest_artifact_id
    )
    assert record.kind == ArtifactKind.LABEL_SET_MANIFEST.value
    assert (record.owner_type, record.owner_id) == (
        ArtifactOwnerType.LABEL_SET.value,
        "gt",
    )
    assert record.checksum == doc.checksum()
    # The stored bytes are the canonical revision, readable only through its pin.
    stored = await harness.derived_store.read_label_set(
        uri=record.uri, checksum=record.checksum
    )
    assert stored == doc
    assert harness.commits == 1


async def test_reimporting_the_same_content_converges(tmp_path) -> None:
    harness = await _harness_with_run(tmp_path)
    doc = label_document("gt", covered=[T0], labels={"a": (T0, 10.0)})
    first = await _import(harness, await write_document(harness, "one.json", doc))
    # Another file, uglier serialization, same content.
    second = await _import(
        harness, await write_document(harness, "two.json", doc, canonical=False)
    )
    assert second.created is False
    assert second.label_set == first.label_set
    assert len(harness.repo.records) == 1


async def test_changed_labels_are_a_new_revision_not_an_overwrite(tmp_path) -> None:
    harness = await _harness_with_run(tmp_path)
    v1 = await _import(
        harness,
        await write_document(
            harness,
            "v1.json",
            label_document("gt", covered=[T0], labels={"a": (T0, 10.0)}),
        ),
    )
    v2 = await _import(
        harness,
        await write_document(
            harness,
            "v2.json",
            label_document("gt", covered=[T0], labels={"a": (T0, 11.0)}),
        ),
    )
    assert v1.label_set.label_set_id == v2.label_set.label_set_id == "gt"
    assert v1.label_set.manifest_checksum != v2.label_set.manifest_checksum
    assert v1.label_set.manifest_artifact_id != v2.label_set.manifest_artifact_id
    # Both revisions stay readable.
    for result in (v1, v2):
        await harness.derived_store.read_label_set(
            uri=result.manifest_uri, checksum=result.label_set.manifest_checksum
        )


async def test_anchors_on_unregistered_robot_runs_are_rejected(tmp_path) -> None:
    harness = await _harness_with_run(tmp_path)
    doc = label_document("gt", run="run-unknown", covered=[T0])
    with pytest.raises(UnknownRobotRunError, match="run-unknown"):
        await _import(harness, await write_document(harness, "gt.json", doc))
    assert harness.repo.records == {}
    assert harness.commits == 0


async def test_the_document_checksum_can_be_pinned(tmp_path) -> None:
    harness = await _harness_with_run(tmp_path)
    doc = label_document("gt", covered=[T0])
    uri = await write_document(harness, "gt.json", doc)
    actual = sha256_checksum(await harness.artifact_store.read_bytes(uri))
    assert (await _import(harness, uri, expected_checksum=actual)).label_count == 0
    with pytest.raises(LabelDocumentChecksumError):
        await _import(harness, uri, expected_checksum="sha256:" + "0" * 64)


async def test_invalid_documents_register_nothing(tmp_path) -> None:
    harness = await _harness_with_run(tmp_path)
    uri = harness.artifact_store.join_uri(harness.root, "incoming", "bad.json")
    await harness.artifact_store.write_bytes(uri, b'{"label_set_id": "gt"}')
    with pytest.raises(Exception, match="invalid label document"):
        await _import(harness, uri)
    with pytest.raises(ValueError, match="not found"):
        await _import(harness, uri + ".missing")
    assert harness.repo.records == {}


async def test_import_never_touches_scenes(tmp_path) -> None:
    harness = await _harness_with_run(tmp_path)
    scene = await harness.register_scene()
    before = scene.manifest_checksum
    doc = label_document("gt", covered=[T0], labels={"a": (T0, 10.0)})
    await _import(harness, await write_document(harness, "gt.json", doc))
    assert harness.scene_store.records[scene.scene_id].manifest_checksum == before
    manifest = await harness.scene_artifact_store.read_pinned_manifest(
        uri=(await harness.artifact_record_store.get(scene.manifest_artifact_id)).uri,
        checksum=before,
    )
    assert not hasattr(manifest, "annotations")
