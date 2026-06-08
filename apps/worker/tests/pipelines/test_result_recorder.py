"""Unit tests for contract-driven result normalization in PipelineTaskResultRecorder."""

from __future__ import annotations

import pytest

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.schemas import (
    PipelineTaskDefinition,
    PipelineTaskOutputKind,
    PipelineTaskOutputSpec,
)
from sceneops_worker.pipelines.result_recorder import normalize_task_outputs

_REF = PipelineTaskOutputKind.REF
_SUMMARY = PipelineTaskOutputKind.SUMMARY
_METRIC = PipelineTaskOutputKind.METRIC
_ARTIFACT = PipelineTaskOutputKind.ARTIFACT


def _task_def(outputs: list[PipelineTaskOutputSpec]) -> PipelineTaskDefinition:
    return PipelineTaskDefinition(
        pipeline_task_id="test_task",
        name="Test Task",
        order=0,
        job_type=JobType.VALIDATE_SCENE,
        outputs=outputs,
    )


class TestNormalizeTaskOutputs:
    def test_ref_output_stored_in_refs(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="scene_index_uri", kind=_REF, source="scene_index_uri"
                ),
            ]
        )
        raw = {"scene_index_uri": "s3://bucket/index.json"}
        result = normalize_task_outputs(raw, task_def)
        assert result.refs == {"scene_index_uri": "s3://bucket/index.json"}
        assert result.summary == {}

    def test_summary_output_stored_in_summary(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="scene_count", kind=_SUMMARY, source="scene_count"
                ),
            ]
        )
        raw = {"scene_count": 42}
        result = normalize_task_outputs(raw, task_def)
        assert result.summary == {"scene_count": 42}
        assert result.refs == {}

    def test_metric_output_stored_in_metrics(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="primary_metric_value",
                    kind=_METRIC,
                    source="primary_metric_value",
                ),
            ]
        )
        raw = {"primary_metric_value": 0.85}
        result = normalize_task_outputs(raw, task_def)
        assert result.metrics == {"primary_metric_value": 0.85}

    def test_artifact_output_stored_in_artifacts(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="package_uri", kind=_ARTIFACT, source="package_uri"
                ),
            ]
        )
        raw = {"package_uri": "s3://bucket/pkg.tar.gz"}
        result = normalize_task_outputs(raw, task_def)
        assert result.artifacts == {"package_uri": "s3://bucket/pkg.tar.gz"}

    def test_target_overrides_storage_key(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="validation_report_uri",
                    kind=_REF,
                    source="report_uri",
                    target="validation_report_uri",
                ),
            ]
        )
        raw = {"report_uri": "s3://bucket/report.json"}
        result = normalize_task_outputs(raw, task_def)
        assert "validation_report_uri" in result.refs
        assert result.refs["validation_report_uri"] == "s3://bucket/report.json"
        assert "report_uri" not in result.refs

    def test_name_used_as_key_when_no_target(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(name="my_ref", kind=_REF, source="some_field"),
            ]
        )
        raw = {"some_field": "value"}
        result = normalize_task_outputs(raw, task_def)
        assert result.refs == {"my_ref": "value"}

    def test_missing_optional_output_skipped(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="optional_uri",
                    kind=_REF,
                    source="optional_uri",
                    required=False,
                ),
            ]
        )
        raw = {}
        result = normalize_task_outputs(raw, task_def)
        assert result.refs == {}

    def test_missing_required_output_raises(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="required_uri", kind=_REF, source="required_uri", required=True
                ),
            ]
        )
        with pytest.raises(ValueError, match="Required output 'required_uri'"):
            normalize_task_outputs({}, task_def)

    def test_default_applied_when_value_absent(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="scene_count",
                    kind=_SUMMARY,
                    source="scene_count",
                    default=0,
                ),
            ]
        )
        raw = {}
        result = normalize_task_outputs(raw, task_def)
        assert result.summary == {"scene_count": 0}

    def test_explicit_value_overrides_default(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="scene_count",
                    kind=_SUMMARY,
                    source="scene_count",
                    default=0,
                ),
            ]
        )
        raw = {"scene_count": 7}
        result = normalize_task_outputs(raw, task_def)
        assert result.summary == {"scene_count": 7}

    def test_dot_path_source_reads_nested_value(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="ap50", kind=_METRIC, source="metrics.ap50"
                ),
            ]
        )
        raw = {"metrics": {"ap50": 0.72}}
        result = normalize_task_outputs(raw, task_def)
        assert result.metrics == {"ap50": 0.72}

    def test_dot_path_source_missing_intermediate_returns_none(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="ap50", kind=_METRIC, source="metrics.ap50"
                ),
            ]
        )
        raw = {"metrics": None}
        result = normalize_task_outputs(raw, task_def)
        assert result.metrics == {}

    def test_no_outputs_produces_empty_normalized(self) -> None:
        task_def = _task_def([])
        raw = {"some_field": "ignored"}
        result = normalize_task_outputs(raw, task_def)
        assert result.refs == {}
        assert result.summary == {}
        assert result.metrics == {}
        assert result.artifacts == {}

    def test_raw_result_always_populated(self) -> None:
        task_def = _task_def([])
        raw = {"foo": "bar", "count": 3}
        result = normalize_task_outputs(raw, task_def)
        assert result.raw_result == {"foo": "bar", "count": 3}

    def test_validate_scene_source_target_mapping(self) -> None:
        """report_uri → validation_report_uri, status → validation_status."""
        task_def = _task_def(
            [
                PipelineTaskOutputSpec(
                    name="validation_report_uri",
                    kind=_REF,
                    source="report_uri",
                    target="validation_report_uri",
                ),
                PipelineTaskOutputSpec(
                    name="validation_status",
                    kind=_SUMMARY,
                    source="status",
                    target="validation_status",
                ),
                PipelineTaskOutputSpec(
                    name="should_block_pipeline",
                    kind=_SUMMARY,
                    source="should_block_pipeline",
                ),
            ]
        )
        raw = {
            "report_uri": "s3://bucket/report.json",
            "status": "failed",
            "should_block_pipeline": True,
        }
        result = normalize_task_outputs(raw, task_def)
        assert result.refs["validation_report_uri"] == "s3://bucket/report.json"
        assert result.summary["validation_status"] == "failed"
        assert result.summary["should_block_pipeline"] is True


class TestNewTaskRequiresNoRecorderChanges:
    """Adding a new fake task with different output fields requires no recorder code changes."""

    def test_new_fake_task_normalizes_correctly(self) -> None:
        fake_outputs = [
            PipelineTaskOutputSpec(name="widget_uri", kind=_REF, source="widget_uri"),
            PipelineTaskOutputSpec(
                name="widget_count", kind=_SUMMARY, source="widget_count"
            ),
            PipelineTaskOutputSpec(
                name="widget_score", kind=_METRIC, source="widget_score"
            ),
        ]
        task_def = PipelineTaskDefinition(
            pipeline_task_id="make_widgets",
            name="Make Widgets",
            order=0,
            job_type=JobType.BUILD_SCENES,  # any job type
            outputs=fake_outputs,
        )
        raw = {
            "widget_uri": "s3://widgets/w.json",
            "widget_count": 5,
            "widget_score": 0.99,
        }
        result = normalize_task_outputs(raw, task_def)
        assert result.refs == {"widget_uri": "s3://widgets/w.json"}
        assert result.summary == {"widget_count": 5}
        assert result.metrics == {"widget_score": 0.99}
