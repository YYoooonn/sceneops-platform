"""Unit tests for Episode quality builder (SceneOps V2 Request 17).

Pure functions — no DB, no async. Mirrors
apps/api/tests/scenes/test_scene_quality.py's convention.
"""

from __future__ import annotations

from sceneops_core.episodes.schemas import EpisodeRecord, EpisodeStatus
from sceneops_core.episodes.schemas.runs import (
    EpisodeProfileRunRecord,
    EpisodeValidationRunRecord,
)
from sceneops_core.runs.schemas import RunStatus

from app.domains.episodes.quality import (
    build_episode_quality,
    compute_episode_readiness,
)
from app.domains.episodes.schemas import EpisodeQualityReadiness


def _episode(**overrides) -> EpisodeRecord:
    defaults = dict(
        episode_id="ep-1",
        dataset_id="d1",
        dataset_version="v1",
        status=EpisodeStatus.REGISTERED,
        frame_count=10,
    )
    defaults.update(overrides)
    return EpisodeRecord(**defaults)


def _validation_run(
    validation_status: str = "ready",
    should_block_pipeline: bool = False,
    **overrides,
) -> EpisodeValidationRunRecord:
    defaults = dict(
        run_id="val-episode-001",
        status=RunStatus.SUCCEEDED,
        episode_id="ep-1",
        validation_status=validation_status,
        should_block_pipeline=should_block_pipeline,
        checked_episode_count=1,
        issue_count=0,
        error_count=0,
        warning_count=0,
    )
    defaults.update(overrides)
    return EpisodeValidationRunRecord(**defaults)


def _profile_run(**overrides) -> EpisodeProfileRunRecord:
    defaults = dict(
        run_id="profile-episode-001",
        status=RunStatus.SUCCEEDED,
        episode_id="ep-1",
        frame_count=10,
        observation_count=7,
        action_count=3,
        observation_channels=["CAM_FRONT", "state.position"],
        action_channels=["steering"],
    )
    defaults.update(overrides)
    return EpisodeProfileRunRecord(**defaults)


# ── readiness: unknown ────────────────────────────────────────────────────────


def test_no_validation_run_produces_unknown():
    result = build_episode_quality(episode=_episode(), validation_run=None)
    assert result.readiness == EpisodeQualityReadiness.UNKNOWN
    assert "validation_missing" in result.blocking_reasons


def test_compute_readiness_consistent_with_builder():
    val = _validation_run(validation_status="warning")
    direct = compute_episode_readiness(val)
    via_builder = build_episode_quality(
        episode=_episode(), validation_run=val
    ).readiness
    assert direct == via_builder


# ── readiness: ready ──────────────────────────────────────────────────────────


def test_ready_validation_status_produces_ready():
    result = build_episode_quality(
        episode=_episode(), validation_run=_validation_run(validation_status="ready")
    )
    assert result.readiness == EpisodeQualityReadiness.READY
    assert result.blocking_reasons == []


# ── readiness: warning ────────────────────────────────────────────────────────


def test_warning_validation_status_produces_warning():
    result = build_episode_quality(
        episode=_episode(),
        validation_run=_validation_run(validation_status="warning", warning_count=1),
    )
    assert result.readiness == EpisodeQualityReadiness.WARNING


# ── readiness: blocked ────────────────────────────────────────────────────────


def test_should_block_pipeline_produces_blocked():
    result = build_episode_quality(
        episode=_episode(),
        validation_run=_validation_run(
            validation_status="ready", should_block_pipeline=True
        ),
    )
    assert result.readiness == EpisodeQualityReadiness.BLOCKED
    assert "validation_blocked" in result.blocking_reasons


def test_failed_validation_status_produces_blocked():
    result = build_episode_quality(
        episode=_episode(),
        validation_run=_validation_run(validation_status="failed"),
    )
    assert result.readiness == EpisodeQualityReadiness.BLOCKED


# ── validation summary ────────────────────────────────────────────────────────


def test_validation_summary_fields_pass_through():
    result = build_episode_quality(
        episode=_episode(),
        validation_run=_validation_run(
            validation_status="warning",
            issue_count=2,
            error_count=0,
            warning_count=2,
        ),
    )
    assert result.validation is not None
    assert result.validation.run_id == "val-episode-001"
    assert result.validation.validation_status == "warning"
    assert result.validation.warning_count == 2
    assert result.validation.issue_count == 2


def test_missing_validation_produces_none_section():
    result = build_episode_quality(episode=_episode(), validation_run=None)
    assert result.validation is None


# ── profile summary ────────────────────────────────────────────────────────────


def test_profile_summary_fields_pass_through():
    result = build_episode_quality(
        episode=_episode(),
        profile_run=_profile_run(),
    )
    assert result.profile is not None
    assert result.profile.run_id == "profile-episode-001"
    assert result.profile.observation_count == 7
    assert result.profile.action_count == 3
    assert "CAM_FRONT" in result.profile.observation_channels


def test_missing_profile_produces_none_section():
    result = build_episode_quality(episode=_episode(), profile_run=None)
    assert result.profile is None


def test_counts_use_profile_run_when_available():
    result = build_episode_quality(
        episode=_episode(frame_count=10), profile_run=_profile_run()
    )
    assert result.counts.frame_count == 10
    assert result.counts.observation_count == 7
    assert result.counts.action_count == 3


def test_counts_have_none_observation_action_without_profile_run():
    result = build_episode_quality(episode=_episode(frame_count=10), profile_run=None)
    assert result.counts.frame_count == 10
    assert result.counts.observation_count is None
    assert result.counts.action_count is None


# ── identity fields ───────────────────────────────────────────────────────────


def test_response_identity_fields():
    result = build_episode_quality(episode=_episode())
    assert result.episode_id == "ep-1"
    assert result.dataset_id == "d1"
    assert result.dataset_version == "v1"
    assert result.status == "registered"


# ── EpisodeStatus is not used as quality gate (SceneOps V2 Request 17 §7) ────


def test_readiness_ignores_episode_status():
    """Unlike Scene (which gates readiness on scene.status), Episode
    readiness is purely a function of the validation run — CREATED and
    REGISTERED episodes with the same validation run get the same readiness."""
    created = build_episode_quality(
        episode=_episode(status=EpisodeStatus.CREATED),
        validation_run=_validation_run(validation_status="ready"),
    )
    registered = build_episode_quality(
        episode=_episode(status=EpisodeStatus.REGISTERED),
        validation_run=_validation_run(validation_status="ready"),
    )
    assert created.readiness == registered.readiness == EpisodeQualityReadiness.READY
