"""Unit tests for build_pipeline_result_from_task_runs — normalized bucket aggregation."""

from __future__ import annotations

from sceneops_core.common.ids import generate_pipeline_task_run_id
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineTaskResult,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
    PipelineType,
)
from sceneops_worker.pipelines.result_builder import (
    build_pipeline_result_from_task_runs,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _pipeline_run(
    pipeline_type: PipelineType = PipelineType.DATASET_SCENE_INGESTION,
) -> PipelineRunManifest:
    now = utc_now()
    return PipelineRunManifest(
        pipeline_run_id="run-001",
        type=pipeline_type,
        status=PipelineRunStatus.RUNNING,
        dataset_id="ds-001",
        dataset_version="v1",
        created_at=now,
        updated_at=now,
    )


def _task_run(
    task_id: str,
    order: int,
    job_type: JobType,
    status: PipelineTaskRunStatus = PipelineTaskRunStatus.SUCCEEDED,
    result: PipelineTaskResult | None = None,
) -> PipelineTaskRunManifest:
    now = utc_now()
    return PipelineTaskRunManifest(
        pipeline_task_run_id=generate_pipeline_task_run_id(),
        pipeline_run_id="run-001",
        pipeline_task_id=task_id,
        pipeline_task_name=task_id,
        task_order=order,
        status=status,
        job_type=job_type,
        result=result,
        created_at=now,
        updated_at=now,
    )


def _task_result(
    task_id: str,
    *,
    refs: dict | None = None,
    summary: dict | None = None,
    metrics: dict | None = None,
    artifacts: dict | None = None,
    raw_result: dict | None = None,
) -> PipelineTaskResult:
    return PipelineTaskResult(
        pipeline_task_id=task_id,
        refs=refs or {},
        summary=summary or {},
        metrics=metrics or {},
        artifacts=artifacts or {},
        raw_result=raw_result or {},
    )


# ── summary tests ─────────────────────────────────────────────────────────────


class TestPipelineSummary:
    def _build(self, task_runs, status=PipelineRunStatus.SUCCEEDED):
        return build_pipeline_result_from_task_runs(
            pipeline_run=_pipeline_run(),
            task_runs=task_runs,
            status=status,
        )

    def test_summary_contains_status(self) -> None:
        result = self._build([])
        assert result.summary["status"] == "succeeded"

    def test_summary_contains_task_counts(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.INGEST_SCENES,
                PipelineTaskRunStatus.SUCCEEDED,
                _task_result("t1"),
            ),
            _task_run(
                "t2",
                1,
                JobType.VALIDATE_SCENE,
                PipelineTaskRunStatus.SKIPPED,
                _task_result("t2"),
            ),
            _task_run(
                "t3",
                2,
                JobType.BUILD_SCENE_INDEX,
                PipelineTaskRunStatus.FAILED,
                _task_result("t3"),
            ),
        ]
        result = self._build(task_runs)
        assert result.summary["task_count"] == 3
        assert result.summary["succeeded_task_count"] == 1
        assert result.summary["skipped_task_count"] == 1
        assert result.summary["failed_task_count"] == 1
        assert result.summary["blocked_task_count"] == 0

    def test_summary_does_not_include_raw_result_fields(self) -> None:
        raw = {
            "class_metrics": {"car": 0.9},
            "metadata": {"foo": "bar"},
            "metrics_uri": "s3://bucket/metrics.json",
            "model_id": "my-model",
            "dataset_id": "ds-001",
        }
        task_runs = [
            _task_run(
                "eval",
                0,
                JobType.EVALUATE_DETECTION,
                result=_task_result(
                    "eval",
                    raw_result=raw,
                ),
            ),
        ]
        result = self._build(task_runs)
        for raw_key in (
            "class_metrics",
            "metadata",
            "metrics_uri",
            "model_id",
            "dataset_id",
        ):
            assert (
                raw_key not in result.summary
            ), f"raw_result key '{raw_key}' leaked into summary"

    def test_summary_does_not_include_task_summary_fields(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.VALIDATE_SCENE,
                result=_task_result(
                    "t1",
                    summary={"should_block_pipeline": False, "issue_count": 0},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "should_block_pipeline" not in result.summary
        assert "issue_count" not in result.summary

    def test_summary_keys_are_exactly_the_pipeline_level_keys(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.BUILD_DATASET_MANIFEST,
                result=_task_result(
                    "t1",
                    refs={"dataset_manifest_uri": "s3://bucket/manifest.json"},
                    summary={"scene_count": 10},
                    metrics={"primary_metric_value": 0.85},
                    raw_result={"extra_field": "should_not_appear"},
                ),
            ),
        ]
        result = self._build(task_runs)
        expected_keys = {
            "status",
            "task_count",
            "succeeded_task_count",
            "skipped_task_count",
            "blocked_task_count",
            "failed_task_count",
        }
        assert set(result.summary.keys()) == expected_keys


# ── outputs tests ─────────────────────────────────────────────────────────────


class TestPipelineOutputs:
    def _build(self, task_runs):
        return build_pipeline_result_from_task_runs(
            pipeline_run=_pipeline_run(),
            task_runs=task_runs,
            status=PipelineRunStatus.SUCCEEDED,
        )

    def test_outputs_built_from_task_refs(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.BUILD_DATASET_MANIFEST,
                result=_task_result(
                    "t1",
                    refs={"dataset_manifest_uri": "s3://bucket/manifest.json"},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert result.outputs["dataset_manifest_uri"] == "s3://bucket/manifest.json"

    def test_outputs_does_not_include_raw_result_keys(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.EVALUATE_DETECTION,
                result=_task_result(
                    "t1",
                    refs={"evaluation_run_id": "eval-001"},
                    raw_result={"class_metrics": {}, "model_id": "m1", "metrics": {}},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "class_metrics" not in result.outputs
        assert "model_id" not in result.outputs
        assert "metrics" not in result.outputs

    def test_later_task_overrides_earlier_on_same_key(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.REGISTER_SCENE,
                result=_task_result(
                    "t1",
                    refs={"scene_manifest_uris": ["s3://early"]},
                ),
            ),
            _task_run(
                "t2",
                1,
                JobType.BUILD_SCENE_INDEX,
                result=_task_result(
                    "t2",
                    refs={"scene_manifest_uris": ["s3://later"]},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert result.outputs["scene_manifest_uris"] == ["s3://later"]

    def test_outputs_merges_refs_from_all_tasks(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.INGEST_SCENES,
                result=_task_result(
                    "t1",
                    refs={"scene_manifest_uris": ["s3://scene.json"]},
                ),
            ),
            _task_run(
                "t2",
                1,
                JobType.BUILD_DATASET_MANIFEST,
                result=_task_result(
                    "t2",
                    refs={"dataset_manifest_uri": "s3://manifest.json"},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "scene_manifest_uris" in result.outputs
        assert "dataset_manifest_uri" in result.outputs

    def test_none_values_excluded_from_outputs(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.VALIDATE_SCENE,
                result=_task_result(
                    "t1",
                    refs={"validation_run_id": None},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "validation_run_id" not in result.outputs


# ── metrics tests ─────────────────────────────────────────────────────────────


class TestPipelineMetrics:
    def _build(self, task_runs):
        return build_pipeline_result_from_task_runs(
            pipeline_run=_pipeline_run(PipelineType.DETECTION_EVALUATION),
            task_runs=task_runs,
            status=PipelineRunStatus.SUCCEEDED,
        )

    def test_metrics_built_from_task_metrics(self) -> None:
        task_runs = [
            _task_run(
                "eval",
                0,
                JobType.EVALUATE_DETECTION,
                result=_task_result(
                    "eval",
                    metrics={
                        "primary_metric_name": "mAP@0.5",
                        "primary_metric_value": 0.72,
                    },
                ),
            ),
        ]
        result = self._build(task_runs)
        assert result.metrics["primary_metric_value"] == 0.72
        assert result.metrics["primary_metric_name"] == "mAP@0.5"

    def test_primary_metric_not_in_summary(self) -> None:
        task_runs = [
            _task_run(
                "eval",
                0,
                JobType.EVALUATE_DETECTION,
                result=_task_result(
                    "eval",
                    metrics={"primary_metric_value": 0.72},
                    summary={"annotation_count": 1000},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "primary_metric_value" not in result.summary

    def test_class_metrics_not_in_pipeline_summary(self) -> None:
        task_runs = [
            _task_run(
                "eval",
                0,
                JobType.EVALUATE_DETECTION,
                result=_task_result(
                    "eval",
                    raw_result={"class_metrics": {"car": 0.9, "pedestrian": 0.6}},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "class_metrics" not in result.summary
        assert "class_metrics" not in result.outputs
        assert "class_metrics" not in result.metrics

    def test_metrics_empty_when_no_task_metrics(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.BUILD_DATASET_MANIFEST,
                result=_task_result(
                    "t1",
                    refs={"dataset_manifest_uri": "s3://manifest.json"},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert result.metrics == {}


# ── artifacts tests ───────────────────────────────────────────────────────────


class TestPipelineArtifacts:
    def _build(self, task_runs):
        return build_pipeline_result_from_task_runs(
            pipeline_run=_pipeline_run(),
            task_runs=task_runs,
            status=PipelineRunStatus.SUCCEEDED,
        )

    def test_lineage_artifacts_from_task_artifacts(self) -> None:
        task_runs = [
            _task_run(
                "val",
                0,
                JobType.VALIDATE_SCENE,
                result=_task_result(
                    "val",
                    artifacts={"validation_report_uri": "s3://bucket/report.json"},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert (
            result.lineage.artifacts["validation_report_uri"]
            == "s3://bucket/report.json"
        )

    def test_artifact_uris_not_leaked_into_outputs(self) -> None:
        task_runs = [
            _task_run(
                "val",
                0,
                JobType.VALIDATE_SCENE,
                result=_task_result(
                    "val",
                    artifacts={"validation_report_uri": "s3://bucket/report.json"},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "validation_report_uri" not in result.outputs

    def test_multiple_task_artifacts_merged(self) -> None:
        task_runs = [
            _task_run(
                "val",
                0,
                JobType.VALIDATE_SCENE,
                result=_task_result(
                    "val",
                    artifacts={"validation_report_uri": "s3://val.json"},
                ),
            ),
            _task_run(
                "prof",
                1,
                JobType.PROFILE_SCENE,
                result=_task_result(
                    "prof",
                    artifacts={"profile_report_uri": "s3://prof.json"},
                ),
            ),
        ]
        result = self._build(task_runs)
        assert "validation_report_uri" in result.lineage.artifacts
        assert "profile_report_uri" in result.lineage.artifacts


# ── task preservation tests ───────────────────────────────────────────────────


class TestTaskPreservation:
    def _build(self, task_runs):
        return build_pipeline_result_from_task_runs(
            pipeline_run=_pipeline_run(),
            task_runs=task_runs,
            status=PipelineRunStatus.SUCCEEDED,
        )

    def test_task_raw_result_preserved_in_tasks_list(self) -> None:
        raw = {"class_metrics": {"car": 0.9}, "metadata": {"version": "v2"}}
        task_runs = [
            _task_run(
                "eval",
                0,
                JobType.EVALUATE_DETECTION,
                result=_task_result(
                    "eval",
                    raw_result=raw,
                ),
            ),
        ]
        result = self._build(task_runs)
        assert len(result.tasks) == 1
        assert result.tasks[0].raw_result == raw

    def test_task_result_buckets_preserved(self) -> None:
        task_runs = [
            _task_run(
                "t1",
                0,
                JobType.VALIDATE_SCENE,
                result=_task_result(
                    "t1",
                    refs={"validation_run_id": "vrun-001"},
                    summary={
                        "validation_status": "ready",
                        "should_block_pipeline": False,
                    },
                    metrics={},
                    artifacts={"validation_report_uri": "s3://report.json"},
                    raw_result={"status": "ready", "should_block_pipeline": False},
                ),
            ),
        ]
        result = self._build(task_runs)
        t = result.tasks[0]
        assert t.refs["validation_run_id"] == "vrun-001"
        assert t.summary["validation_status"] == "ready"
        assert t.artifacts["validation_report_uri"] == "s3://report.json"
        assert t.raw_result["status"] == "ready"

    def test_task_without_result_gets_placeholder(self) -> None:
        tr = _task_run("t1", 0, JobType.BUILD_DATASET_MANIFEST)
        tr.job_id = "job-abc"
        result = self._build([tr])
        assert len(result.tasks) == 1
        assert result.tasks[0].pipeline_task_id == "t1"
        assert result.tasks[0].job_id == "job-abc"


# ── detection evaluation pipeline integration ─────────────────────────────────


class TestDetectionEvaluationPipelineResult:
    """Verify the detection_evaluation pipeline result shape end-to-end."""

    def test_full_detection_pipeline_result(self) -> None:
        task_runs = [
            _task_run(
                "predict_detection",
                0,
                JobType.PREDICT_DETECTION,
                result=_task_result(
                    "predict_detection",
                    refs={"inference_run_id": "inf-001"},
                    artifacts={
                        "prediction_manifest_uri": "s3://pred/manifest.json",
                        "predictions_root_uri": "s3://pred/",
                    },
                    summary={"sample_count": 50, "prediction_count": 120},
                ),
            ),
            _task_run(
                "evaluate_detection",
                1,
                JobType.EVALUATE_DETECTION,
                result=_task_result(
                    "evaluate_detection",
                    refs={
                        "evaluation_run_id": "eval-001",
                        "inference_run_id": "inf-001",
                    },
                    artifacts={
                        "evaluation_manifest_uri": "s3://eval/manifest.json",
                        "metrics_uri": "s3://eval/metrics.json",
                    },
                    summary={
                        "annotation_count": 200,
                        "prediction_count": 120,
                        "ground_truth_count": 200,
                        "evaluation_unit": "annotation",
                    },
                    metrics={
                        "primary_metric_name": "mAP@0.5",
                        "primary_metric_value": 0.72,
                    },
                    raw_result={
                        "class_metrics": {"car": 0.9},
                        "metrics": {"mAP@0.5": 0.72},
                        "model_id": "my-model",
                        "dataset_id": "ds-001",
                        "summary": {"match_distance_m": 2.0},
                        "metadata": {},
                    },
                ),
            ),
        ]

        pipeline_run = _pipeline_run(PipelineType.DETECTION_EVALUATION)
        result = build_pipeline_result_from_task_runs(
            pipeline_run=pipeline_run,
            task_runs=task_runs,
            status=PipelineRunStatus.SUCCEEDED,
        )

        # Summary: only pipeline-level fields
        assert result.summary["status"] == "succeeded"
        assert result.summary["task_count"] == 2
        for noisy_key in (
            "class_metrics",
            "model_id",
            "dataset_id",
            "metadata",
            "summary",
            "metrics",
        ):
            assert noisy_key not in result.summary, f"'{noisy_key}' leaked into summary"

        # Outputs: only refs
        assert result.outputs["evaluation_run_id"] == "eval-001"
        assert result.outputs["inference_run_id"] == "inf-001"
        assert "metrics_uri" not in result.outputs
        assert "evaluation_manifest_uri" not in result.outputs
        assert "class_metrics" not in result.outputs

        # Metrics: primary metric value from task
        assert result.metrics["primary_metric_value"] == 0.72
        assert result.metrics["primary_metric_name"] == "mAP@0.5"

        # Artifacts: file URIs
        assert "evaluation_manifest_uri" in result.lineage.artifacts
        assert "metrics_uri" in result.lineage.artifacts
        assert "prediction_manifest_uri" in result.lineage.artifacts

        # Tasks: raw_result preserved
        eval_task = next(
            t for t in result.tasks if t.pipeline_task_id == "evaluate_detection"
        )
        assert "class_metrics" in eval_task.raw_result
        assert "model_id" in eval_task.raw_result
