from __future__ import annotations

from sceneops_core.datasets.schemas import (
    DatasetVersionRecord,
    EpisodeVersionSummary,
    SceneVersionSummary,
)


def test_summaries_default_to_none_on_bare_record():
    record = DatasetVersionRecord(dataset_id="d", version="v1")
    assert record.scene is None
    assert record.episode is None


def test_scene_only_record():
    record = DatasetVersionRecord(
        dataset_id="d", version="v1", scene=SceneVersionSummary(scene_count=5)
    )
    assert record.scene is not None
    assert record.episode is None


def test_episode_only_record():
    record = DatasetVersionRecord(
        dataset_id="d", version="v1", episode=EpisodeVersionSummary(episode_count=2)
    )
    assert record.scene is None
    assert record.episode is not None


def test_mixed_record():
    record = DatasetVersionRecord(
        dataset_id="d",
        version="v1",
        scene=SceneVersionSummary(scene_count=5),
        episode=EpisodeVersionSummary(episode_count=2),
    )
    assert record.scene is not None
    assert record.episode is not None


def test_is_unset_true_for_defaults():
    assert SceneVersionSummary().is_unset()
    assert EpisodeVersionSummary().is_unset()


def test_is_unset_false_when_any_field_set():
    assert not SceneVersionSummary(observed_channels=["CAM_FRONT"]).is_unset()
    assert not SceneVersionSummary(keyframe_count=1).is_unset()
    assert not EpisodeVersionSummary(episode_count=1).is_unset()


def test_a_summary_holds_membership_projections_only():
    assert set(SceneVersionSummary.model_fields) == {
        "scene_count",
        "keyframe_count",
        "observation_count",
        "observed_channels",
    }


def test_dataset_version_record_json_round_trip_with_summaries():
    record = DatasetVersionRecord(
        dataset_id="d",
        version="v1",
        scene=SceneVersionSummary(scene_count=5, observed_channels=["CAM_FRONT"]),
        episode=EpisodeVersionSummary(episode_count=2),
    )
    restored = DatasetVersionRecord.model_validate(record.model_dump(mode="json"))
    assert restored == record


def test_scene_summary_has_no_quality_cache():
    """Readiness is derived from run records for each Scene's current
    revision, never cached on the DatasetVersion (ADR-007 §16)."""
    for name in (
        "latest_validation_run_id",
        "validation_status",
        "should_block_pipeline",
        "validation_report_uri",
        "latest_profile_run_id",
        "profile_report_uri",
    ):
        assert name not in SceneVersionSummary.model_fields
