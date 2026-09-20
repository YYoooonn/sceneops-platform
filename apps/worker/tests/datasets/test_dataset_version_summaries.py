"""Tests for the DatasetVersion scene/episode summary split
(SceneOps V2 Request 02/03/04).

A DatasetVersion is scene-only, episode-only, mixed, or neither depending on
whether the underlying flat columns for each domain are still at their
untouched defaults — see SceneVersionSummary.is_unset()/
EpisodeVersionSummary.is_unset(). As of Request 03, DatasetVersionRecord has
no flat top-level duplicates of these fields at all — scene/episode are the
only way to read them. Request 04 additionally moved raw_source_root_uri
into scene (Scene-only; Episode sources come from RobotRun.mcap_uri) and
dropped the dead latest_distribution_run_id/distribution_report_uri fields
entirely (no SQL columns for them anymore).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sceneops_db.converters.datasets import (
    dataset_version_model_to_record,
    dataset_version_record_to_values,
)
from sceneops_db.models.datasets import DatasetVersionModel

_NOW = datetime.now(timezone.utc)


def _model(**overrides) -> DatasetVersionModel:
    base = dict(
        id="d:v1",
        dataset_id="d",
        version="v1",
        status="registered",
        manifest_uri=None,
        scene_count=0,
        sample_count=0,
        frame_count=0,
        episode_count=0,
        channels=[],
        required_channels=[],
        source_dataset_id=None,
        source_dataset_version=None,
        raw_source_root_uri=None,
        latest_validation_run_id=None,
        validation_status=None,
        should_block_pipeline=None,
        validation_report_uri=None,
        latest_profile_run_id=None,
        profile_report_uri=None,
        created_at=_NOW,
        updated_at=_NOW,
        metadata_={},
    )
    base.update(overrides)
    return DatasetVersionModel(**base)


class TestSceneOnly:
    def test_scene_populated_episode_none(self) -> None:
        record = dataset_version_model_to_record(
            _model(
                scene_count=5, sample_count=10, frame_count=20, channels=["CAM_FRONT"]
            )
        )
        assert record.scene is not None
        assert record.scene.scene_count == 5
        assert record.scene.channels == ["CAM_FRONT"]
        assert record.episode is None

    def test_scene_populated_via_quality_cache_alone(self) -> None:
        """Counts can be zero (e.g. validate_scene ran before any scenes were
        counted) — a quality-cache field alone must still count as scene
        activity, not just the count fields."""
        record = dataset_version_model_to_record(
            _model(latest_validation_run_id="run-1")
        )
        assert record.scene is not None
        assert record.episode is None


class TestEpisodeOnly:
    def test_episode_populated_scene_none(self) -> None:
        record = dataset_version_model_to_record(_model(episode_count=3))
        assert record.scene is None
        assert record.episode is not None
        assert record.episode.episode_count == 3


class TestMixed:
    def test_both_populated(self) -> None:
        record = dataset_version_model_to_record(_model(scene_count=2, episode_count=1))
        assert record.scene is not None
        assert record.scene.scene_count == 2
        assert record.episode is not None
        assert record.episode.episode_count == 1


class TestNeither:
    def test_untouched_version_has_no_summaries(self) -> None:
        record = dataset_version_model_to_record(_model())
        assert record.scene is None
        assert record.episode is None


class TestConfigOnlyScene:
    """SceneOps V2 Request 03 §5: a non-None `scene` is not proof that Scene
    data was built — required_channels alone (set via the dataset-version
    API before any scene job runs) is enough to make the summary non-default."""

    def test_required_channels_alone_creates_scene_summary(self) -> None:
        record = dataset_version_model_to_record(
            _model(required_channels=["CAM_FRONT"])
        )
        assert record.scene is not None
        assert record.scene.scene_count == 0  # no scenes actually built
        assert record.scene.required_channels == ["CAM_FRONT"]


class TestRoundTrip:
    """flat SQL model -> nested record -> flat values preserves every field
    (SceneOps V2 Request 03)."""

    def test_full_round_trip_preserves_all_fields(self) -> None:
        model = _model(
            status="registered",
            manifest_uri="s3://bucket/manifest.json",
            scene_count=5,
            sample_count=10,
            frame_count=20,
            channels=["CAM_FRONT"],
            required_channels=["CAM_FRONT"],
            episode_count=3,
            source_dataset_id="src-d",
            source_dataset_version="src-v1",
            raw_source_root_uri="/data/raw/x",
            latest_validation_run_id="run-val-1",
            validation_status="ready",
            should_block_pipeline=False,
            validation_report_uri="s3://bucket/val.json",
            latest_profile_run_id="run-prof-1",
            profile_report_uri="s3://bucket/prof.json",
        )
        record = dataset_version_model_to_record(model)
        values = dataset_version_record_to_values(record)

        for field in (
            "dataset_id",
            "version",
            "status",
            "manifest_uri",
            "scene_count",
            "sample_count",
            "frame_count",
            "channels",
            "required_channels",
            "episode_count",
            "source_dataset_id",
            "source_dataset_version",
            "raw_source_root_uri",
            "latest_validation_run_id",
            "should_block_pipeline",
            "validation_report_uri",
            "latest_profile_run_id",
            "profile_report_uri",
        ):
            assert values[field] == getattr(model, field), field
        assert values["validation_status"] == model.validation_status
