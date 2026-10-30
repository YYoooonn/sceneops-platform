from __future__ import annotations

from typing import Any

from sceneops_core.sources import SourceContentRecord, SourceSnapshotRecord

from sceneops_db.models.sources import SourceContentModel, SourceSnapshotModel


def source_content_model_to_record(model: SourceContentModel) -> SourceContentRecord:
    return SourceContentRecord(
        content_id=model.content_id,
        format=model.format,
        content_checksum=model.content_checksum,
        inventory_artifact_id=model.inventory_artifact_id,
        file_count=model.file_count,
        total_bytes=model.total_bytes,
        registered_at=model.registered_at,
    )


def source_content_record_to_values(record: SourceContentRecord) -> dict[str, Any]:
    # registered_at is the database's insert time.
    return record.model_dump(exclude={"registered_at"})


def source_snapshot_model_to_record(
    model: SourceSnapshotModel,
) -> SourceSnapshotRecord:
    return SourceSnapshotRecord(
        snapshot_id=model.snapshot_id,
        content_id=model.content_id,
        format=model.format,
        upstream=model.upstream,
        upstream_revision=model.upstream_revision,
        selection_checksum=model.selection_checksum,
        descriptor_artifact_id=model.descriptor_artifact_id,
        descriptor_checksum=model.descriptor_checksum,
        registered_at=model.registered_at,
    )


def source_snapshot_record_to_values(record: SourceSnapshotRecord) -> dict[str, Any]:
    return record.model_dump(exclude={"registered_at"})
