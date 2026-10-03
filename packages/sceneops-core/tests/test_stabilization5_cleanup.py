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


def test_episode_status_is_registration_lifecycle_only():
    """EpisodeStatus deliberately stays CREATED/REGISTERED — see
    apps/api/app/domains/episodes/quality.py's module docstring. BUILT/
    VALIDATED/FAILED were removed: zero write sites, zero persisted rows."""
    assert {member.value for member in EpisodeStatus} == {"created", "registered"}


def test_scene_record_has_no_status():
    """Record existence is registration; quality lives in run records that
    pin the revision they assessed (ADR-007 §13.4, I-16). Neither a status
    enum nor a status field may come back."""
    import sceneops_core.scenes.schemas.enums as scene_enums
    from sceneops_core.scenes.schemas import SceneRecord

    assert not hasattr(scene_enums, "SceneStatus")
    assert "status" not in SceneRecord.model_fields


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
