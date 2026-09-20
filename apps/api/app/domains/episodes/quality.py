"""Episode quality response builder (SceneOps V2 Request 17).

Pure functions — receive already-fetched records, return
EpisodeQualityResponse. Independently testable without a running service or
DB, matching apps/api/app/domains/scenes/quality.py's convention.

Readiness is derived purely from EpisodeRecord + the latest validation/
profile run — NOT from EpisodeRecord.status the way Scene gates on
scene.status in {VALIDATED, PROFILED}. EpisodeStatus deliberately stays a
resource-registration lifecycle only (CREATED/REGISTERED) — see SceneOps V2
Request 17 §7 — so quality/readiness lives entirely in this derivation, not
in the record's own status field.
"""

from __future__ import annotations

from sceneops_core.episodes.schemas import EpisodeProfileRunRecord, EpisodeRecord
from sceneops_core.episodes.schemas.runs import EpisodeValidationRunRecord

from app.domains.episodes.schemas import (
    EpisodeProfileQualitySummary,
    EpisodeQualityCounts,
    EpisodeQualityReadiness,
    EpisodeQualityResponse,
    EpisodeValidationQualitySummary,
)


def build_episode_quality(
    episode: EpisodeRecord,
    validation_run: EpisodeValidationRunRecord | None = None,
    profile_run: EpisodeProfileRunRecord | None = None,
) -> EpisodeQualityResponse:
    readiness, blocking_reasons = _compute_readiness(validation_run)

    return EpisodeQualityResponse(
        episode_id=episode.episode_id,
        dataset_id=episode.dataset_id,
        dataset_version=episode.dataset_version,
        status=str(getattr(episode.status, "value", episode.status)),
        counts=EpisodeQualityCounts(
            frame_count=episode.frame_count or 0,
            observation_count=profile_run.observation_count
            if profile_run is not None
            else None,
            action_count=profile_run.action_count if profile_run is not None else None,
        ),
        validation=_build_validation_summary(validation_run),
        profile=_build_profile_summary(profile_run),
        readiness=readiness,
        blocking_reasons=blocking_reasons,
    )


def compute_episode_readiness(
    validation_run: EpisodeValidationRunRecord | None,
) -> EpisodeQualityReadiness:
    readiness, _ = _compute_readiness(validation_run)
    return readiness


def _compute_readiness(
    validation_run: EpisodeValidationRunRecord | None,
) -> tuple[EpisodeQualityReadiness, list[str]]:
    if validation_run is None:
        return EpisodeQualityReadiness.UNKNOWN, ["validation_missing"]

    val_status = (validation_run.validation_status or "").lower()

    if validation_run.should_block_pipeline or val_status in ("failed", "error"):
        return EpisodeQualityReadiness.BLOCKED, ["validation_blocked"]

    if val_status == "warning":
        return EpisodeQualityReadiness.WARNING, ["validation_warning"]

    if val_status == "ready":
        return EpisodeQualityReadiness.READY, []

    return EpisodeQualityReadiness.UNKNOWN, ["validation_missing"]


def _build_validation_summary(
    run: EpisodeValidationRunRecord | None,
) -> EpisodeValidationQualitySummary | None:
    if run is None:
        return None
    return EpisodeValidationQualitySummary(
        run_id=run.run_id,
        status=str(getattr(run.status, "value", run.status)),
        validation_status=run.validation_status,
        should_block_pipeline=run.should_block_pipeline,
        checked_episode_count=run.checked_episode_count,
        blocking_issue_count=run.error_count,
        warning_count=run.warning_count,
        issue_count=run.issue_count,
        report_uri=run.validation_report_uri,
    )


def _build_profile_summary(
    run: EpisodeProfileRunRecord | None,
) -> EpisodeProfileQualitySummary | None:
    if run is None:
        return None
    return EpisodeProfileQualitySummary(
        run_id=run.run_id,
        status=str(getattr(run.status, "value", run.status)),
        frame_count=run.frame_count,
        observation_count=run.observation_count,
        action_count=run.action_count,
        observation_channels=list(run.observation_channels or []),
        action_channels=list(run.action_channels or []),
        control_frequency_hz=run.control_frequency_hz,
        duration_us=run.duration_us,
        task=run.task,
        outcome=run.outcome,
        profile_report_uri=run.profile_report_uri,
    )
