from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneops_core.datasets.schemas import (
    DatasetVersionRecord,
    EpisodeVersionSummary,
    SceneVersionSummary,
)


CHECKSUM = "sha256:" + "c" * 64


def test_a_record_is_a_committed_composition():
    record = DatasetVersionRecord(
        dataset_id="d", version="v1", composition_checksum=CHECKSUM
    )
    assert (record.scene, record.episode, record.selector) == (None, None, None)
    with pytest.raises(ValidationError):
        DatasetVersionRecord(dataset_id="d", version="v1")
    with pytest.raises(ValidationError):
        DatasetVersionRecord(dataset_id="d", version="v1", composition_checksum="abc")
    for removed in ("status", "updated_at"):
        assert removed not in DatasetVersionRecord.model_fields
        with pytest.raises(ValidationError):
            DatasetVersionRecord(
                dataset_id="d",
                version="v1",
                composition_checksum=CHECKSUM,
                **{removed: "registered"},
            )


def test_scene_only_record():
    record = DatasetVersionRecord(
        dataset_id="d",
        version="v1",
        composition_checksum=CHECKSUM,
        scene=SceneVersionSummary(scene_count=5),
    )
    assert record.scene is not None
    assert record.episode is None


def test_episode_only_record():
    record = DatasetVersionRecord(
        dataset_id="d",
        version="v1",
        composition_checksum=CHECKSUM,
        episode=EpisodeVersionSummary(episode_count=2),
    )
    assert record.scene is None
    assert record.episode is not None


def test_mixed_record():
    record = DatasetVersionRecord(
        dataset_id="d",
        version="v1",
        composition_checksum=CHECKSUM,
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
        composition_checksum=CHECKSUM,
        scene=SceneVersionSummary(scene_count=5, observed_channels=["CAM_FRONT"]),
        episode=EpisodeVersionSummary(episode_count=2),
    )
    restored = DatasetVersionRecord.model_validate(record.model_dump(mode="json"))
    assert restored == record


def test_scene_summary_has_no_quality_cache():
    """Readiness is derived from the run records of the revision each member
    pins, never cached on the DatasetVersion."""
    for name in (
        "latest_validation_run_id",
        "validation_status",
        "should_block_pipeline",
        "validation_report_uri",
        "latest_profile_run_id",
        "profile_report_uri",
    ):
        assert name not in SceneVersionSummary.model_fields
