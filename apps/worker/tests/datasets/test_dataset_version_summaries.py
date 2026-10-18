"""The flat ``dataset_versions`` row <-> nested DatasetVersionRecord
converters.

A DatasetVersion is scene-only, episode-only or mixed depending on which
domains it has members of; a domain with none has no summary (its columns
are zero). scene/episode are the only way to read the summaries. A
DatasetVersion carries no source location, source format or lifecycle
status (ADR-015 §7).
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
        composition_checksum="sha256:" + "c" * 64,
        selector=None,
        scene_count=0,
        keyframe_count=0,
        observation_count=0,
        episode_count=0,
        observed_channels=[],
        created_at=_NOW,
    )
    base.update(overrides)
    return DatasetVersionModel(**base)


class TestSceneOnly:
    def test_scene_populated_episode_none(self) -> None:
        record = dataset_version_model_to_record(
            _model(
                scene_count=5,
                keyframe_count=10,
                observation_count=20,
                observed_channels=["CAM_FRONT"],
            )
        )
        assert record.scene is not None
        assert record.scene.scene_count == 5
        assert record.scene.observed_channels == ["CAM_FRONT"]
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


class TestSelector:
    def test_selector_round_trips_through_jsonb(self) -> None:
        selector = {
            "sources": [
                {
                    "domain": "scene",
                    "robot_run_id": "run-1",
                    "producer_fingerprint": "sha256:" + "f" * 64,
                }
            ]
        }
        record = dataset_version_model_to_record(
            _model(scene_count=1, selector=selector)
        )
        assert record.selector.sources[0].robot_run_id == "run-1"
        assert dataset_version_record_to_values(record)["selector"] == selector
        assert dataset_version_model_to_record(_model(episode_count=1)).selector is None


class TestNoWorkflowConfiguration:
    """A DatasetVersion is membership and scope only: channel requirements and
    free-form metadata live in pipeline / job parameters (ADR-007 §16)."""

    def test_the_row_and_the_record_carry_neither(self) -> None:
        model = _model()
        assert not hasattr(model, "required_channels")
        assert not hasattr(model, "metadata_")
        record = dataset_version_model_to_record(model)
        assert "metadata" not in record.model_dump()
        assert "required_channels" not in str(record.model_dump())


class TestRoundTrip:
    """flat SQL model -> nested record -> flat values preserves every field
    (SceneOps V2 Request 03)."""

    def test_full_round_trip_preserves_all_fields(self) -> None:
        model = _model(
            scene_count=5,
            keyframe_count=10,
            observation_count=20,
            observed_channels=["CAM_FRONT"],
            episode_count=3,
        )
        record = dataset_version_model_to_record(model)
        values = dataset_version_record_to_values(record)

        for field in (
            "dataset_id",
            "version",
            "composition_checksum",
            "selector",
            "scene_count",
            "keyframe_count",
            "observation_count",
            "observed_channels",
            "episode_count",
        ):
            assert values[field] == getattr(model, field), field
        for removed in (
            "status",
            "updated_at",
            "manifest_uri",
            "input_source_root_uri",
            "source_dataset_id",
            "source_dataset_version",
        ):
            assert removed not in values
            assert not hasattr(model, removed)
