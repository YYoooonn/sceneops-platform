"""Episode quality response builder.

Pure functions — receive already-fetched records, return
EpisodeQualityResponse. Only run records that assessed the Episode's
current manifest revision count; any other run passed in is ignored, so a
replaced Episode reports ``unknown`` until its new revision is validated
(ADR-007 §13.4).
"""

from __future__ import annotations

from typing import TypeVar

from sceneops_core.episodes import EpisodeReadiness, derive_episode_readiness
from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRecord,
    EpisodeValidationRunRecord,
)

from app.domains.episodes.schemas import (
    EpisodeProfileQualitySummary,
    EpisodeQualityCounts,
    EpisodeQualityReadiness,
    EpisodeQualityResponse,
    EpisodeValidationQualitySummary,
)

_R = TypeVar("_R", EpisodeValidationRunRecord, EpisodeProfileRunRecord)

_BLOCKING_REASON = {
    EpisodeReadiness.UNKNOWN: ["validation_missing"],
    EpisodeReadiness.BLOCKED: ["validation_blocked"],
    EpisodeReadiness.WARNING: ["validation_warning"],
    EpisodeReadiness.READY: [],
}


def _current(episode: EpisodeRecord, run: _R | None) -> _R | None:
    if run is None or not run.assessed(
        manifest_artifact_id=episode.manifest_artifact_id,
        manifest_checksum=episode.manifest_checksum,
    ):
        return None
    return run


def build_episode_quality(
    episode: EpisodeRecord,
    validation_run: EpisodeValidationRunRecord | None = None,
    profile_run: EpisodeProfileRunRecord | None = None,
) -> EpisodeQualityResponse:
    validation_run = _current(episode, validation_run)
    profile_run = _current(episode, profile_run)
    readiness = derive_episode_readiness(validation_run)
    return EpisodeQualityResponse(
        episode_id=episode.episode_id,
        dataset_id=episode.dataset_id,
        dataset_version=episode.dataset_version,
        manifest_artifact_id=episode.manifest_artifact_id,
        manifest_checksum=episode.manifest_checksum,
        counts=EpisodeQualityCounts(
            observation_count=episode.observation_count,
            state_count=episode.state_count,
            action_count=episode.action_count,
            event_count=episode.event_count,
        ),
        validation=_validation_summary(validation_run),
        profile=_profile_summary(profile_run),
        readiness=EpisodeQualityReadiness(readiness.value),
        blocking_reasons=list(_BLOCKING_REASON[readiness]),
    )


def _validation_summary(
    run: EpisodeValidationRunRecord | None,
) -> EpisodeValidationQualitySummary | None:
    if run is None:
        return None
    return EpisodeValidationQualitySummary(
        run_id=run.run_id,
        status=str(getattr(run.status, "value", run.status)),
        validation_status=run.validation_status,
        should_block_pipeline=run.should_block_pipeline,
        blocking_issue_count=run.error_count,
        warning_count=run.warning_count,
        issue_count=run.issue_count,
        report_uri=run.validation_report_uri,
    )


def _profile_summary(
    run: EpisodeProfileRunRecord | None,
) -> EpisodeProfileQualitySummary | None:
    if run is None:
        return None
    return EpisodeProfileQualitySummary(
        run_id=run.run_id,
        status=str(getattr(run.status, "value", run.status)),
        observation_count=run.observation_count,
        state_count=run.state_count,
        action_count=run.action_count,
        event_count=run.event_count,
        window_duration_ns=run.window_duration_ns,
        profile_report_uri=run.profile_report_uri,
    )
