"""Locks in Stabilization Request 5's dead/reserved-architecture cleanup
decisions so they don't silently drift back. Not exhaustive coverage of the
removed code (there was none to begin with) — just enough to catch
accidental re-additions or partial reverts.
"""

from __future__ import annotations

import pytest

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType


def test_episode_record_has_no_status_task_or_outcome():
    """Record existence is registration and readiness lives in revision-
    pinned run records (ADR-007 §13.4); a canonical Episode carries no
    manufactured task or outcome label (§31)."""
    import sceneops_core.episodes.schemas.enums as episode_enums
    from sceneops_core.episodes.schemas import EpisodeManifest, EpisodeRecord

    assert not hasattr(episode_enums, "EpisodeStatus")
    for field in ("status", "task", "outcome"):
        assert field not in EpisodeRecord.model_fields
        assert field not in EpisodeManifest.model_fields


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


def test_artifact_reservations_for_unbuilt_capabilities_do_not_exist():
    """Scene comparison, auto-labeling and scene / dataset export have no
    JobType, so no ArtifactOwnerType or ArtifactKind is reserved for them."""
    owners = {member.value for member in ArtifactOwnerType}
    kinds = {member.value for member in ArtifactKind}
    assert not owners & {
        "scene_comparison_run",
        "scene_export_run",
        "scene_auto_label_run",
        "dataset_export_run",
        "dataset_auto_label_run",
    }
    assert not kinds & {
        "scene_package",
        "world_state_manifest",
        "auto_label_manifest",
        "auto_label_report",
    }
