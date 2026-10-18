from __future__ import annotations

from typing import Any

from sceneops_core.datasets.schemas import RevisionSelector
from sceneops_core.datasets.schemas.records import DatasetRecord, DatasetVersionRecord
from sceneops_core.datasets.schemas.summaries import (
    EpisodeVersionSummary,
    SceneVersionSummary,
)

from sceneops_db.models.datasets import DatasetModel, DatasetVersionModel


def make_dataset_version_id(dataset_id: str, version: str) -> str:
    return f"{dataset_id}:{version}"


# ── Dataset ──────────────────────────────────────────────────────────────────


def dataset_model_to_record(model: DatasetModel) -> DatasetRecord:
    return DatasetRecord(
        dataset_id=model.dataset_id,
        name=model.name,
        description=model.description,
        default_version=model.default_version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def dataset_record_to_values(record: DatasetRecord) -> dict[str, Any]:
    return {
        "dataset_id": record.dataset_id,
        "name": record.name,
        "description": record.description,
        "default_version": record.default_version,
    }


# ── DatasetVersion ────────────────────────────────────────────────────────────


def _scene_version_summary_from_model(
    model: DatasetVersionModel,
) -> SceneVersionSummary | None:
    summary = SceneVersionSummary(
        scene_count=model.scene_count,
        keyframe_count=model.keyframe_count,
        observation_count=model.observation_count,
        observed_channels=model.observed_channels or [],
    )
    return None if summary.is_unset() else summary


def _episode_version_summary_from_model(
    model: DatasetVersionModel,
) -> EpisodeVersionSummary | None:
    summary = EpisodeVersionSummary(episode_count=model.episode_count)
    return None if summary.is_unset() else summary


def dataset_version_model_to_record(
    model: DatasetVersionModel,
) -> DatasetVersionRecord:
    return DatasetVersionRecord(
        dataset_id=model.dataset_id,
        version=model.version,
        composition_checksum=model.composition_checksum,
        selector=(
            RevisionSelector.model_validate(model.selector)
            if model.selector is not None
            else None
        ),
        created_at=model.created_at,
        scene=_scene_version_summary_from_model(model),
        episode=_episode_version_summary_from_model(model),
    )


def dataset_version_record_to_values(
    record: DatasetVersionRecord,
) -> dict[str, Any]:
    """Flatten a DatasetVersionRecord to the columns of its one INSERT. An
    absent domain summary is that domain's zero summary: the columns have no
    default, so a composition always states every count."""
    scene = record.scene or SceneVersionSummary()
    episode = record.episode or EpisodeVersionSummary()
    values: dict[str, Any] = {
        "id": make_dataset_version_id(record.dataset_id, record.version),
        "dataset_id": record.dataset_id,
        "version": record.version,
        "composition_checksum": record.composition_checksum,
        "selector": (
            record.selector.model_dump(mode="json")
            if record.selector is not None
            else None
        ),
        "scene_count": scene.scene_count,
        "keyframe_count": scene.keyframe_count,
        "observation_count": scene.observation_count,
        "observed_channels": scene.observed_channels,
        "episode_count": episode.episode_count,
    }
    if record.created_at is not None:
        values["created_at"] = record.created_at
    return values
