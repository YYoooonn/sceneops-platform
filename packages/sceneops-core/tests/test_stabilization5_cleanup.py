"""Locks in Stabilization Request 5's dead/reserved-architecture cleanup
decisions so they don't silently drift back. Not exhaustive coverage of the
removed code (there was none to begin with) — just enough to catch
accidental re-additions or partial reverts.
"""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.episodes.schemas.enums import EpisodeStatus
from sceneops_core.scenes.schemas.enums import SceneStatus


def test_episode_status_is_registration_lifecycle_only():
    """EpisodeStatus deliberately stays CREATED/REGISTERED — see
    apps/api/app/domains/episodes/quality.py's module docstring. BUILT/
    VALIDATED/FAILED were removed: zero write sites, zero persisted rows."""
    assert {member.value for member in EpisodeStatus} == {"created", "registered"}


def test_scene_status_keeps_build_validate_profile_lifecycle():
    """Unlike Episode, Scene's architecture does fold validation/profile
    outcomes into status (validate_scene.py/profile_scene.py write these).
    VALIDATING/PROFILING (superseded by RunStatus.RUNNING on the
    corresponding run record) and DEPRECATED (no write path) were removed.
    BUILT is intentionally NOT renamed to REGISTERED — see the Stabilization
    Request 5 decision register; this is a documented naming asymmetry with
    Episode, not a bug."""
    assert {member.value for member in SceneStatus} == {
        "created",
        "built",
        "validated",
        "profiled",
        "failed",
    }


def test_paths_module_was_removed():
    with pytest.raises(ModuleNotFoundError):
        import sceneops_core.paths  # noqa: F401


def test_check_distribution_job_type_was_removed():
    from sceneops_core.jobs.schemas import JobType

    assert not hasattr(JobType, "CHECK_DISTRIBUTION")
    assert "check_distribution" not in {member.value for member in JobType}


def test_distribution_artifact_enums_were_removed_alongside_check_distribution():
    """Confirms JobType.CHECK_DISTRIBUTION wasn't removed while leaving its
    matching artifact enums behind (a half-removed feature) — both go
    together, same as the JobType itself."""
    assert "dataset_distribution_run" not in {m.value for m in ArtifactOwnerType}
    assert "distribution_report" not in {m.value for m in ArtifactKind}


def test_reserved_job_types_still_have_matching_artifact_evidence():
    """The 5 JobType values with no registered handler are retained
    specifically because each has a matching ArtifactOwnerType (or
    ArtifactKind) reservation — this is the concrete evidence standard the
    cleanup decision register applied. If any of these matching values ever
    get removed too, revisit whether the JobType should go with them."""
    assert ArtifactOwnerType.SCENE_COMPARISON_RUN
    assert ArtifactOwnerType.SCENE_AUTO_LABEL_RUN
    assert ArtifactOwnerType.SCENE_EXPORT_RUN
    assert ArtifactKind.SCENE_PACKAGE
    assert ArtifactOwnerType.DATASET_AUTO_LABEL_RUN
    assert ArtifactOwnerType.DATASET_EXPORT_RUN
