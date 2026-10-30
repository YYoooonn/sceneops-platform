"""The Source Registry decision table (ADR-015 §3, §5, MS-6a), decided from
records alone."""

from __future__ import annotations

import pytest

from sceneops_core.sources import (
    ContentFormatConflictError,
    InconsistentSourceRegistryError,
    RegistrationOutcome,
    SnapshotContentConflictError,
    SnapshotDeclaration,
    SnapshotDescriptorConflictError,
    SnapshotSelection,
    SourceContentInventory,
    SourceContentRecord,
    SourceFileEntry,
    SourceSnapshotDescriptor,
    SourceSnapshotRecord,
    decide_content_registration,
    decide_snapshot_registration,
)

_INVENTORY = SourceContentInventory.create(
    [
        SourceFileEntry(path="a.bin", size_bytes=3, sha256="sha256:" + "1" * 64),
        SourceFileEntry(path="b/c.json", size_bytes=5, sha256="sha256:" + "2" * 64),
    ]
)


def _content(**overrides) -> SourceContentRecord:
    record = SourceContentRecord.for_inventory(
        _INVENTORY, format="lerobot", inventory_artifact_id="art-inv"
    )
    return record.model_copy(update=overrides)


def _descriptor(*, content_id=_INVENTORY.content_id, license="mit"):
    return SourceSnapshotDescriptor(
        declaration=SnapshotDeclaration(
            format="lerobot",
            upstream="lerobot/pusht",
            upstream_revision="rev-1",
            selection=SnapshotSelection(include=("**",)),
        ),
        content_id=content_id,
        annotations={"license": license},
    )


def _snapshot(descriptor=None) -> SourceSnapshotRecord:
    return SourceSnapshotRecord.for_descriptor(
        descriptor or _descriptor(), descriptor_artifact_id="art-desc"
    )


def test_records_project_inventory_and_descriptor() -> None:
    content = _content()
    assert (content.content_id, content.file_count, content.total_bytes) == (
        _INVENTORY.content_id,
        2,
        8,
    )
    snapshot = _snapshot()
    assert snapshot.snapshot_id == _descriptor().snapshot_id
    assert snapshot.descriptor_checksum == _descriptor().descriptor_checksum
    assert snapshot.selection_checksum == (
        _descriptor().declaration.selection.selection_checksum
    )


def test_absent_records_are_created_and_identical_ones_exist() -> None:
    assert decide_content_registration(_content(), None) == RegistrationOutcome.CREATED
    assert (
        decide_content_registration(_content(), _content())
        == RegistrationOutcome.EXISTING
    )
    assert (
        decide_snapshot_registration(_snapshot(), None) == RegistrationOutcome.CREATED
    )
    assert (
        decide_snapshot_registration(_snapshot(), _snapshot())
        == RegistrationOutcome.EXISTING
    )


def test_registered_at_is_not_identity() -> None:
    from datetime import UTC, datetime

    stored = _content(registered_at=datetime(2026, 10, 22, tzinfo=UTC))
    assert (
        decide_content_registration(_content(), stored) == RegistrationOutcome.EXISTING
    )


def test_same_bytes_under_another_format_conflict() -> None:
    with pytest.raises(ContentFormatConflictError):
        decide_content_registration(_content(format="nuscenes"), _content())


def test_one_declaration_with_two_byte_sets_conflicts() -> None:
    other = _snapshot(_descriptor(content_id="content-" + "f" * 32))
    with pytest.raises(SnapshotContentConflictError):
        decide_snapshot_registration(other, _snapshot())


def test_one_declaration_with_two_descriptors_conflicts() -> None:
    other = _snapshot(_descriptor(license="apache-2.0"))
    with pytest.raises(SnapshotDescriptorConflictError):
        decide_snapshot_registration(other, _snapshot())


def test_a_registry_row_that_disagrees_with_its_id_is_inconsistent() -> None:
    with pytest.raises(InconsistentSourceRegistryError, match="file_count"):
        decide_content_registration(_content(), _content(file_count=3))
    with pytest.raises(InconsistentSourceRegistryError, match="upstream_revision"):
        decide_snapshot_registration(
            _snapshot(), _snapshot().model_copy(update={"upstream_revision": "x"})
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_id": "content-short"},
        {"format": "Not An Identifier"},
        {"content_checksum": "md5:abc"},
        {"file_count": 0},
    ],
)
def test_content_record_fields_are_validated(overrides) -> None:
    with pytest.raises(ValueError):
        SourceContentRecord(**{**_content().model_dump(), **overrides})
