"""Tests for Phase 2 wiring: BuildScenesJobResult + _BUILD_SCENES_OUTPUTS.

Verifies that:
- BuildScenesJobResult carries the 6 new grouping-report fields
- normalize_task_outputs places them in task.result.summary (not refs/artifacts)
- Existing fields (scene_manifest_uris REF, diagnostic ARTIFACTs) are unchanged
- missing_channel_counts_by_channel (a dict) is placed in summary, not elsewhere
"""

from __future__ import annotations

from sceneops_core.jobs.schemas.results.scene import BuildScenesJobResult
from sceneops_core.pipelines.builtin import RAW_LOG_SCENE_BUILDING_PIPELINE
from sceneops_worker.pipelines.result_recorder import normalize_task_outputs

# Grab the build_scenes task definition from the canonical pipeline definition.
_BUILD_SCENES_TASK_DEF = next(
    t
    for t in RAW_LOG_SCENE_BUILDING_PIPELINE.tasks
    if t.pipeline_task_id == "build_scenes"
)


def _raw_result(**overrides) -> dict:
    """Minimal valid BuildScenesJobResult serialization with Phase 2 fields."""
    base = BuildScenesJobResult(
        scene_manifest_uris=["s3://bucket/scenes/sc-001/manifest.json"],
        scene_count=1,
        sample_count=10,
        frame_count=60,
        scene_segment_index_uri="s3://bucket/segments.json",
        raw_log_manifest_uri="s3://bucket/raw/manifest.json",
        raw_log_frame_index_uri="s3://bucket/raw/frame_index.json",
        observation_count=60,
        source_type="nuscenes_raw_log_mock",
        source_format="nuscenes",
        segmentation_strategy="fixed_window",
        sampling_strategy="time_bucket",
        sample_count_before_filtering=10,
        sample_count_after_filtering=10,
        dropped_sample_count=0,
        warned_sample_count=0,
        samples_with_missing_channels_count=0,
        missing_channel_counts_by_channel={},
    )
    data = base.model_dump(mode="python")
    data.update(overrides)
    return data


class TestBuildScenesResultDefaults:
    def test_new_fields_have_zero_defaults(self) -> None:
        result = BuildScenesJobResult()
        assert result.sample_count_before_filtering == 0
        assert result.sample_count_after_filtering == 0
        assert result.dropped_sample_count == 0
        assert result.warned_sample_count == 0
        assert result.samples_with_missing_channels_count == 0
        assert result.missing_channel_counts_by_channel == {}

    def test_new_fields_serialized(self) -> None:
        result = BuildScenesJobResult(
            sample_count_before_filtering=5,
            sample_count_after_filtering=4,
            dropped_sample_count=1,
            warned_sample_count=2,
            samples_with_missing_channels_count=1,
            missing_channel_counts_by_channel={"CAM_FRONT": 1},
        )
        data = result.model_dump(mode="python")
        assert data["sample_count_before_filtering"] == 5
        assert data["sample_count_after_filtering"] == 4
        assert data["dropped_sample_count"] == 1
        assert data["warned_sample_count"] == 2
        assert data["samples_with_missing_channels_count"] == 1
        assert data["missing_channel_counts_by_channel"] == {"CAM_FRONT": 1}


class TestBuildScenesOutputContract:
    def _normalize(self, raw: dict):
        return normalize_task_outputs(raw, _BUILD_SCENES_TASK_DEF)

    def test_new_summary_fields_normalized(self) -> None:
        raw = _raw_result(
            sample_count_before_filtering=10,
            sample_count_after_filtering=10,
            dropped_sample_count=0,
            warned_sample_count=0,
            samples_with_missing_channels_count=0,
            missing_channel_counts_by_channel={},
        )
        result = self._normalize(raw)
        assert result.summary["sample_count_before_filtering"] == 10
        assert result.summary["sample_count_after_filtering"] == 10
        assert result.summary["dropped_sample_count"] == 0
        assert result.summary["warned_sample_count"] == 0
        assert result.summary["samples_with_missing_channels_count"] == 0
        assert result.summary["missing_channel_counts_by_channel"] == {}

    def test_missing_channel_dict_in_summary_not_refs_or_artifacts(self) -> None:
        raw = _raw_result(missing_channel_counts_by_channel={"CAM_FRONT": 3})
        result = self._normalize(raw)
        assert "missing_channel_counts_by_channel" in result.summary
        assert "missing_channel_counts_by_channel" not in result.refs
        assert "missing_channel_counts_by_channel" not in result.artifacts

    def test_new_fields_not_in_refs_or_artifacts(self) -> None:
        raw = _raw_result()
        result = self._normalize(raw)
        new_fields = {
            "sample_count_before_filtering",
            "sample_count_after_filtering",
            "dropped_sample_count",
            "warned_sample_count",
            "samples_with_missing_channels_count",
            "missing_channel_counts_by_channel",
        }
        for field in new_fields:
            assert field not in result.refs, f"{field!r} leaked into refs"
            assert field not in result.artifacts, f"{field!r} leaked into artifacts"

    def test_existing_ref_scene_manifest_uris_unchanged(self) -> None:
        raw = _raw_result()
        result = self._normalize(raw)
        assert result.refs["scene_manifest_uris"] == [
            "s3://bucket/scenes/sc-001/manifest.json"
        ]

    def test_existing_summary_fields_unchanged(self) -> None:
        raw = _raw_result()
        result = self._normalize(raw)
        assert result.summary["scene_count"] == 1
        assert result.summary["sample_count"] == 10
        assert result.summary["frame_count"] == 60
        assert result.summary["observation_count"] == 60
        assert result.summary["segmentation_strategy"] == "fixed_window"
        assert result.summary["sampling_strategy"] == "time_bucket"

    def test_existing_artifacts_unchanged(self) -> None:
        raw = _raw_result()
        result = self._normalize(raw)
        assert (
            result.artifacts["scene_segment_index_uri"] == "s3://bucket/segments.json"
        )
        assert (
            result.artifacts["raw_log_manifest_uri"] == "s3://bucket/raw/manifest.json"
        )
        assert (
            result.artifacts["raw_log_frame_index_uri"]
            == "s3://bucket/raw/frame_index.json"
        )

    def test_before_equals_after_in_phase2(self) -> None:
        """In Phase 2 (no policy enforcement), before == after == sample_count."""
        raw = _raw_result(
            sample_count=10,
            sample_count_before_filtering=10,
            sample_count_after_filtering=10,
        )
        result = self._normalize(raw)
        assert (
            result.summary["sample_count_before_filtering"]
            == result.summary["sample_count_after_filtering"]
            == result.summary["sample_count"]
        )
