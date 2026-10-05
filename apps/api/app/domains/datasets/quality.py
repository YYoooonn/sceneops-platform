"""Dataset version quality response builder.

Both dataset quality endpoints share the same aggregate source of truth:
  - build_dataset_scene_quality_aggregate: aggregates scene quality rows
  - build_dataset_version_quality_from_aggregate: builds the compact operator view

DatasetVersionQualityResponse is a scene-aggregate summary, not a latest-run cache.
It reports canonical Scene quality (validation readiness, observed channels,
counts) only. Labels and detection selectability are derived label-set and
sample-view concerns, not Scene quality (ADR-007 §33).
"""

from __future__ import annotations

from sceneops_core.datasets.schemas.records import DatasetVersionRecord

from app.domains.datasets.schemas import (
    DatasetProfileSummary,
    DatasetQualityReadiness,
    DatasetSceneQualityAggregateSummary,
    DatasetSceneQualitySectionSummary,
    DatasetValidationSummary,
    DatasetVersionQualityCounts,
    DatasetVersionQualityResponse,
)
from app.domains.scenes.schemas import SceneQualityReadiness, SceneQualityResponse


def compute_dataset_readiness_from_aggregate(
    summary: DatasetSceneQualityAggregateSummary,
) -> DatasetQualityReadiness:
    if summary.scene_count == 0:
        return DatasetQualityReadiness.UNKNOWN

    if summary.unknown_scene_count == summary.scene_count:
        return DatasetQualityReadiness.UNKNOWN

    if summary.blocked_scene_count == summary.scene_count:
        return DatasetQualityReadiness.BLOCKED

    if (
        summary.warning_scene_count > 0
        or summary.blocked_scene_count > 0
        or summary.unknown_scene_count > 0
    ):
        return DatasetQualityReadiness.WARNING

    return DatasetQualityReadiness.READY


def build_dataset_version_quality_from_aggregate(
    version: DatasetVersionRecord,
    summary: DatasetSceneQualityAggregateSummary,
) -> DatasetVersionQualityResponse:
    readiness = compute_dataset_readiness_from_aggregate(summary)
    scene_count = summary.scene_count

    return DatasetVersionQualityResponse(
        dataset_id=version.dataset_id,
        version=version.version,
        status=str(getattr(version.status, "value", version.status)),
        readiness=readiness,
        counts=DatasetVersionQualityCounts(
            scene_count=scene_count,
            keyframe_count=summary.total_keyframe_count,
            observation_count=summary.total_observation_count,
        ),
        scene_quality=DatasetSceneQualitySectionSummary(
            ready_scene_count=summary.ready_scene_count,
            warning_scene_count=summary.warning_scene_count,
            blocked_scene_count=summary.blocked_scene_count,
            unknown_scene_count=summary.unknown_scene_count,
            observed_channels=summary.observed_channels,
        ),
        validation=DatasetValidationSummary(
            ready_scene_count=summary.ready_scene_count,
            warning_scene_count=summary.warning_scene_count,
            blocked_scene_count=summary.blocked_scene_count,
            unknown_scene_count=summary.unknown_scene_count,
        ),
        profile=DatasetProfileSummary(
            observed_channels=summary.observed_channels,
        ),
    )


def build_dataset_scene_quality_aggregate(
    all_quality: list[SceneQualityResponse],
) -> DatasetSceneQualityAggregateSummary:
    ready = warning = blocked = unknown = 0
    total_keyframes = total_observations = 0
    channels: set[str] = set()

    for q in all_quality:
        if q.readiness == SceneQualityReadiness.READY:
            ready += 1
        elif q.readiness == SceneQualityReadiness.WARNING:
            warning += 1
        elif q.readiness == SceneQualityReadiness.BLOCKED:
            blocked += 1
        else:
            unknown += 1

        total_keyframes += q.counts.keyframe_count
        total_observations += q.counts.observation_count

        if q.profile is not None:
            channels.update(q.profile.observed_channels)

    return DatasetSceneQualityAggregateSummary(
        scene_count=len(all_quality),
        ready_scene_count=ready,
        warning_scene_count=warning,
        blocked_scene_count=blocked,
        unknown_scene_count=unknown,
        total_keyframe_count=total_keyframes,
        total_observation_count=total_observations,
        observed_channels=sorted(channels),
    )
