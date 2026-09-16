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
    assert not SceneVersionSummary(channels=["CAM_FRONT"]).is_unset()
    assert not SceneVersionSummary(latest_validation_run_id="run-1").is_unset()
    assert not EpisodeVersionSummary(episode_count=1).is_unset()


def test_config_only_scene_summary_does_not_imply_data_exists():
    """required_channels alone (declared before any scene job ran) makes the
    summary non-unset, but scene_count staying 0 is the actual signal that no
    Scene data has been built yet — see SceneOps V2 Request 03 §5."""
    summary = SceneVersionSummary(required_channels=["CAM_FRONT"])
    assert not summary.is_unset()
    assert summary.scene_count == 0
    assert summary.manifest_uri is None


def test_dataset_version_record_json_round_trip_with_summaries():
    record = DatasetVersionRecord(
        dataset_id="d",
        version="v1",
        scene=SceneVersionSummary(scene_count=5, channels=["CAM_FRONT"]),
        episode=EpisodeVersionSummary(episode_count=2),
    )
    restored = DatasetVersionRecord.model_validate(record.model_dump(mode="json"))
    assert restored == record
