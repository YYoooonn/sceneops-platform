"""Dataset version quality response builder.

Both dataset quality endpoints share the same aggregate source of truth:
  - build_dataset_scene_quality_aggregate: aggregates scene quality rows
  - build_dataset_version_quality_from_aggregate: builds the compact operator view

DatasetVersionQualityResponse is a scene-aggregate summary, not a latest-run cache.
"""

from __future__ import annotations

from sceneops_core.datasets.schemas.records import DatasetVersionRecord

from app.domains.datasets.schemas import (
    DatasetGroundTruthSummary,
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

    if summary.selectable_for_detection_count == 0:
        return DatasetQualityReadiness.BLOCKED

    if (
        summary.warning_scene_count > 0
        or summary.blocked_scene_count > 0
        or summary.unknown_scene_count > 0
        or summary.non_selectable_for_detection_count > 0
    ):
        return DatasetQualityReadiness.WARNING

    return DatasetQualityReadiness.READY


def build_dataset_version_quality_from_aggregate(
    version: DatasetVersionRecord,
    summary: DatasetSceneQualityAggregateSummary,
) -> DatasetVersionQualityResponse:
    readiness = compute_dataset_readiness_from_aggregate(summary)
    scene_count = summary.scene_count
    coverage_ratio = (
        summary.ground_truth_scene_count / scene_count if scene_count > 0 else 0.0
    )

    return DatasetVersionQualityResponse(
        dataset_id=version.dataset_id,
        version=version.version,
        status=str(getattr(version.status, "value", version.status)),
        readiness=readiness,
        counts=DatasetVersionQualityCounts(
            scene_count=scene_count,
            sample_count=summary.total_sample_count,
            frame_count=summary.total_frame_count,
            annotation_count=summary.total_annotation_count,
            ground_truth_scene_count=summary.ground_truth_scene_count,
            selectable_scene_count=summary.selectable_for_detection_count,
        ),
        scene_quality=DatasetSceneQualitySectionSummary(
            ready_scene_count=summary.ready_scene_count,
            warning_scene_count=summary.warning_scene_count,
            blocked_scene_count=summary.blocked_scene_count,
            unknown_scene_count=summary.unknown_scene_count,
            selectable_for_detection_count=summary.selectable_for_detection_count,
            non_selectable_for_detection_count=summary.non_selectable_for_detection_count,
            exclusion_reason_counts=summary.exclusion_reason_counts,
            observed_channels=summary.observed_channels,
        ),
        ground_truth=DatasetGroundTruthSummary(
            has_ground_truth=summary.ground_truth_scene_count > 0,
            ground_truth_scene_count=summary.ground_truth_scene_count,
            annotated_scene_count=summary.annotated_scene_count,
            annotation_count=summary.total_annotation_count,
            ground_truth_coverage_ratio=round(coverage_ratio, 4),
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
        manifest_uri=version.manifest_uri,
    )


def build_dataset_scene_quality_aggregate(
    all_quality: list[SceneQualityResponse],
) -> DatasetSceneQualityAggregateSummary:
    ready = warning = blocked = unknown = 0
    selectable = non_selectable = 0
    gt_scenes = annotated_scenes = 0
    total_samples = total_frames = total_annotations = 0
    exclusion_counts: dict[str, int] = {}
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

        if q.selectable_for_detection:
            selectable += 1
        else:
            non_selectable += 1

        if q.ground_truth.has_ground_truth:
            gt_scenes += 1
        if (q.ground_truth.annotation_count or 0) > 0:
            annotated_scenes += 1

        total_samples += q.counts.sample_count
        total_frames += q.counts.frame_count
        total_annotations += q.counts.annotation_count or 0

        for reason in q.exclusion_reasons:
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

        if q.profile is not None:
            channels.update(q.profile.observed_channels)

    return DatasetSceneQualityAggregateSummary(
        scene_count=len(all_quality),
        ready_scene_count=ready,
        warning_scene_count=warning,
        blocked_scene_count=blocked,
        unknown_scene_count=unknown,
        selectable_for_detection_count=selectable,
        non_selectable_for_detection_count=non_selectable,
        ground_truth_scene_count=gt_scenes,
        annotated_scene_count=annotated_scenes,
        total_sample_count=total_samples,
        total_frame_count=total_frames,
        total_annotation_count=total_annotations,
        exclusion_reason_counts=exclusion_counts,
        observed_channels=sorted(channels),
    )
