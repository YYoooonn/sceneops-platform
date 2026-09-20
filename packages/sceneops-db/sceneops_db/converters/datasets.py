from __future__ import annotations

from typing import Any

from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.summaries import (
    EpisodeVersionSummary,
    SceneVersionSummary,
)

from sceneops_db.models.datasets import DatasetModel, DatasetVersionModel

from ._utils import (
    enum_to_value,
    metadata_from_model,
    values_with_metadata,
)


def make_dataset_version_id(dataset_id: str, version: str) -> str:
    return f"{dataset_id}:{version}"


# ── Dataset ──────────────────────────────────────────────────────────────────


def dataset_model_to_record(model: DatasetModel) -> DatasetRecord:
    return DatasetRecord(
        dataset_id=model.dataset_id,
        name=model.name,
        description=model.description,
        type=model.type,
        default_version=model.default_version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
    )


def dataset_record_to_values(record: DatasetRecord) -> dict[str, Any]:
    return {
        "dataset_id": record.dataset_id,
        "name": record.name,
        "description": record.description,
        "type": enum_to_value(record.type),
        "default_version": record.default_version,
        "metadata_": record.metadata or {},
    }


# ── DatasetVersion ────────────────────────────────────────────────────────────


def _scene_version_summary_from_model(
    model: DatasetVersionModel,
) -> SceneVersionSummary | None:
    summary = SceneVersionSummary(
        scene_count=model.scene_count,
        sample_count=model.sample_count,
        frame_count=model.frame_count,
        channels=model.channels or [],
        required_channels=model.required_channels or [],
        manifest_uri=model.manifest_uri,
        raw_source_root_uri=model.raw_source_root_uri,
        latest_validation_run_id=model.latest_validation_run_id,
        validation_status=model.validation_status,
        should_block_pipeline=model.should_block_pipeline,
        validation_report_uri=model.validation_report_uri,
        latest_profile_run_id=model.latest_profile_run_id,
        profile_report_uri=model.profile_report_uri,
    )
    return None if summary.is_unset() else summary


def _episode_version_summary_from_model(
    model: DatasetVersionModel,
) -> EpisodeVersionSummary | None:
    summary = EpisodeVersionSummary(episode_count=model.episode_count)
    return None if summary.is_unset() else summary


def _scene_summary_to_values(summary: SceneVersionSummary) -> dict[str, Any]:
    return {
        "scene_count": summary.scene_count,
        "sample_count": summary.sample_count,
        "frame_count": summary.frame_count,
        "channels": summary.channels,
        "required_channels": summary.required_channels,
        "manifest_uri": summary.manifest_uri,
        "raw_source_root_uri": summary.raw_source_root_uri,
        "latest_validation_run_id": summary.latest_validation_run_id,
        "validation_status": enum_to_value(summary.validation_status),
        "should_block_pipeline": summary.should_block_pipeline,
        "validation_report_uri": summary.validation_report_uri,
        "latest_profile_run_id": summary.latest_profile_run_id,
        "profile_report_uri": summary.profile_report_uri,
    }


def _episode_summary_to_values(summary: EpisodeVersionSummary) -> dict[str, Any]:
    return {"episode_count": summary.episode_count}


def dataset_version_model_to_record(
    model: DatasetVersionModel,
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_id=model.dataset_id,
        version=model.version,
        status=model.status,
        source_dataset_id=model.source_dataset_id,
        source_dataset_version=model.source_dataset_version,
        created_at=model.created_at,
        updated_at=model.updated_at,
        metadata=metadata_from_model(model),
        scene=_scene_version_summary_from_model(model),
        episode=_episode_version_summary_from_model(model),
    )


def dataset_version_record_to_values(
    record: DatasetVersionRecord,
) -> dict[str, Any]:
    """Flatten a DatasetVersionRecord back to dataset_versions columns.

    Scene/episode-owned columns are only included when the corresponding
    summary is present on the record. This matters for create() (an absent
    summary lets the SQL column server_default apply) and, more importantly,
    for update() (an absent summary leaves the existing column value alone
    instead of resetting it to a summary's zero-value defaults) — see
    SceneOps V2 Request 03. Production writers never rely on this path for
    domain-summary changes though; they go through
    update_scene_summary()/update_episode_summary() exclusively, which do a
    true per-field partial update. This function's scene/episode handling is
    a correctness backstop, not the primary write path.
    """
    values: dict[str, Any] = {
        "id": make_dataset_version_id(record.dataset_id, record.version),
        "dataset_id": record.dataset_id,
        "version": record.version,
        "status": enum_to_value(record.status),
        "source_dataset_id": record.source_dataset_id,
        "source_dataset_version": record.source_dataset_version,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "metadata": record.metadata,
    }
    if record.scene is not None:
        values.update(_scene_summary_to_values(record.scene))
    if record.episode is not None:
        values.update(_episode_summary_to_values(record.episode))
    return values_with_metadata(values)
