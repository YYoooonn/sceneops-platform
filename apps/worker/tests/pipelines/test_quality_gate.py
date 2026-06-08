"""Unit tests for rule-based PipelineQualityGate."""

from __future__ import annotations

import pytest

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.schemas import (
    PipelineTaskDefinition,
    PipelineTaskQualityRule,
    PipelineTaskQualityRuleType,
    PipelineTaskResult,
    PipelineTaskRunManifest,
    PipelineTaskRunStatus,
)
from sceneops_core.common.time import utc_now
from sceneops_core.common.ids import generate_pipeline_task_run_id
from sceneops_worker.pipelines.errors import PipelineQualityBlocked
from sceneops_worker.pipelines.quality_gate import PipelineQualityGate


def _task_def(quality_rules: list[PipelineTaskQualityRule]) -> PipelineTaskDefinition:
    return PipelineTaskDefinition(
        pipeline_task_id="test_task",
        name="Test Task",
        order=0,
        job_type=JobType.VALIDATE_SCENE,
        quality_rules=quality_rules,
    )


def _task_run_with_result(result: PipelineTaskResult) -> PipelineTaskRunManifest:
    now = utc_now()
    return PipelineTaskRunManifest(
        pipeline_task_run_id=generate_pipeline_task_run_id(),
        pipeline_run_id="run-001",
        pipeline_task_id="test_task",
        pipeline_task_name="Test Task",
        task_order=0,
        status=PipelineTaskRunStatus.SUCCEEDED,
        job_type=JobType.VALIDATE_SCENE,
        result=result,
        created_at=now,
        updated_at=now,
    )


def _result(
    *,
    summary: dict | None = None,
    refs: dict | None = None,
    metrics: dict | None = None,
) -> PipelineTaskResult:
    return PipelineTaskResult(
        pipeline_task_id="test_task",
        refs=refs or {},
        summary=summary or {},
        metrics=metrics or {},
    )


class TestBlockIfTrue:
    def setup_method(self) -> None:
        self.gate = PipelineQualityGate()

    def test_blocks_when_value_is_true(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
                    source="summary.should_block_pipeline",
                    message="Validation blocked",
                    code="validate_scene_blocked",
                ),
            ]
        )
        task_run = _task_run_with_result(
            _result(summary={"should_block_pipeline": True})
        )
        with pytest.raises(PipelineQualityBlocked) as exc_info:
            self.gate.check_task_result(task_definition=task_def, task_run=task_run)
        assert exc_info.value.code == "validate_scene_blocked"
        assert "Validation blocked" in str(exc_info.value)

    def test_passes_when_value_is_false(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
                    source="summary.should_block_pipeline",
                ),
            ]
        )
        task_run = _task_run_with_result(
            _result(summary={"should_block_pipeline": False})
        )
        self.gate.check_task_result(task_definition=task_def, task_run=task_run)

    def test_passes_when_value_is_absent(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
                    source="summary.should_block_pipeline",
                ),
            ]
        )
        task_run = _task_run_with_result(_result(summary={}))
        self.gate.check_task_result(task_definition=task_def, task_run=task_run)

    def test_blocks_on_nonzero_count(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
                    source="summary.error_count",
                    code="error_count_nonzero",
                ),
            ]
        )
        task_run = _task_run_with_result(_result(summary={"error_count": 3}))
        with pytest.raises(PipelineQualityBlocked) as exc_info:
            self.gate.check_task_result(task_definition=task_def, task_run=task_run)
        assert exc_info.value.code == "error_count_nonzero"


class TestBlockIfEquals:
    def setup_method(self) -> None:
        self.gate = PipelineQualityGate()

    def test_blocks_when_value_equals_target(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_EQUALS,
                    source="summary.validation_status",
                    value="failed",
                    code="status_failed",
                ),
            ]
        )
        task_run = _task_run_with_result(
            _result(summary={"validation_status": "failed"})
        )
        with pytest.raises(PipelineQualityBlocked) as exc_info:
            self.gate.check_task_result(task_definition=task_def, task_run=task_run)
        assert exc_info.value.code == "status_failed"

    def test_passes_when_value_does_not_equal_target(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_EQUALS,
                    source="summary.validation_status",
                    value="failed",
                ),
            ]
        )
        task_run = _task_run_with_result(
            _result(summary={"validation_status": "ready"})
        )
        self.gate.check_task_result(task_definition=task_def, task_run=task_run)


class TestBlockIfIn:
    def setup_method(self) -> None:
        self.gate = PipelineQualityGate()

    def test_blocks_when_value_in_list(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_IN,
                    source="summary.status",
                    value=["failed", "error"],
                    code="bad_status",
                ),
            ]
        )
        task_run = _task_run_with_result(_result(summary={"status": "error"}))
        with pytest.raises(PipelineQualityBlocked) as exc_info:
            self.gate.check_task_result(task_definition=task_def, task_run=task_run)
        assert exc_info.value.code == "bad_status"

    def test_passes_when_value_not_in_list(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_IN,
                    source="summary.status",
                    value=["failed", "error"],
                ),
            ]
        )
        task_run = _task_run_with_result(_result(summary={"status": "ready"}))
        self.gate.check_task_result(task_definition=task_def, task_run=task_run)


class TestNoRulesNeverBlocks:
    def setup_method(self) -> None:
        self.gate = PipelineQualityGate()

    def test_no_rules_always_passes(self) -> None:
        task_def = _task_def([])
        task_run = _task_run_with_result(
            _result(summary={"should_block_pipeline": True})
        )
        self.gate.check_task_result(task_definition=task_def, task_run=task_run)

    def test_null_result_always_passes(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
                    source="summary.should_block_pipeline",
                ),
            ]
        )
        now = utc_now()
        task_run = PipelineTaskRunManifest(
            pipeline_task_run_id=generate_pipeline_task_run_id(),
            pipeline_run_id="run-001",
            pipeline_task_id="test_task",
            pipeline_task_name="Test Task",
            task_order=0,
            status=PipelineTaskRunStatus.SUCCEEDED,
            job_type=JobType.VALIDATE_SCENE,
            result=None,
            created_at=now,
            updated_at=now,
        )
        self.gate.check_task_result(task_definition=task_def, task_run=task_run)

    def test_default_code_when_message_is_none(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
                    source="summary.should_block_pipeline",
                    message=None,
                    code="quality_gate_blocked",
                ),
            ]
        )
        task_run = _task_run_with_result(
            _result(summary={"should_block_pipeline": True})
        )
        with pytest.raises(PipelineQualityBlocked) as exc_info:
            self.gate.check_task_result(task_definition=task_def, task_run=task_run)
        assert exc_info.value.code == "quality_gate_blocked"


class TestSourcePathReading:
    """Quality gate reads from refs, summary, metrics, artifacts, raw_result buckets."""

    def setup_method(self) -> None:
        self.gate = PipelineQualityGate()

    def test_reads_from_refs(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_EQUALS,
                    source="refs.validation_run_id",
                    value="",
                    code="missing_run_id",
                ),
            ]
        )
        task_run = _task_run_with_result(_result(refs={"validation_run_id": ""}))
        with pytest.raises(PipelineQualityBlocked):
            self.gate.check_task_result(task_definition=task_def, task_run=task_run)

    def test_reads_from_metrics(self) -> None:
        task_def = _task_def(
            [
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_EQUALS,
                    source="metrics.primary_metric_value",
                    value=0.0,
                    code="zero_metric",
                ),
            ]
        )
        task_run = _task_run_with_result(_result(metrics={"primary_metric_value": 0.0}))
        with pytest.raises(PipelineQualityBlocked):
            self.gate.check_task_result(task_definition=task_def, task_run=task_run)


class TestNotJobTypeSpecific:
    """A new fake task with rules requires no changes to PipelineQualityGate."""

    def test_new_fake_task_with_quality_rule(self) -> None:
        gate = PipelineQualityGate()

        fake_task_def = PipelineTaskDefinition(
            pipeline_task_id="make_widgets",
            name="Make Widgets",
            order=0,
            job_type=JobType.BUILD_SCENES,
            quality_rules=[
                PipelineTaskQualityRule(
                    rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
                    source="summary.widget_failure",
                    code="widget_failed",
                ),
            ],
        )

        failing_run = _task_run_with_result(_result(summary={"widget_failure": True}))
        passing_run = _task_run_with_result(_result(summary={"widget_failure": False}))

        with pytest.raises(PipelineQualityBlocked) as exc_info:
            gate.check_task_result(task_definition=fake_task_def, task_run=failing_run)
        assert exc_info.value.code == "widget_failed"

        gate.check_task_result(task_definition=fake_task_def, task_run=passing_run)
