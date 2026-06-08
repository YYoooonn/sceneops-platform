from __future__ import annotations

from typing import Any

from sceneops_core.pipelines.schemas import (
    PipelineTaskDefinition,
    PipelineTaskQualityRule,
    PipelineTaskQualityRuleType,
    PipelineTaskResult,
    PipelineTaskRunManifest,
)
from sceneops_worker.pipelines.errors import PipelineQualityBlocked


def _read_dot_path(data: dict, path: str) -> Any:
    """Read a value from a nested dict using a dot-separated path."""
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _read_result_value(result: PipelineTaskResult, source: str) -> Any:
    """Read a value from the normalized task result view using a dot-path source."""
    view = {
        "refs": result.refs,
        "summary": result.summary,
        "metrics": result.metrics,
        "artifacts": result.artifacts,
        "raw_result": result.raw_result,
    }
    return _read_dot_path(view, source)


class PipelineQualityGate:
    """Evaluates quality rules declared on a task definition.

    Raises PipelineQualityBlocked when any rule matches.
    Tasks with no quality_rules always pass.
    """

    def check_task_result(
        self,
        *,
        task_definition: PipelineTaskDefinition,
        task_run: PipelineTaskRunManifest,
    ) -> None:
        result = task_run.result
        if result is None or not task_definition.quality_rules:
            return

        for rule in task_definition.quality_rules:
            self._evaluate_rule(rule, result)

    def _evaluate_rule(
        self,
        rule: PipelineTaskQualityRule,
        result: PipelineTaskResult,
    ) -> None:
        value = _read_result_value(result, rule.source)

        if self._matches(rule, value):
            message = rule.message or f"Quality gate blocked: {rule.source}={value!r}"
            raise PipelineQualityBlocked(message=message, code=rule.code)

    def _matches(self, rule: PipelineTaskQualityRule, value: Any) -> bool:
        if rule.rule_type == PipelineTaskQualityRuleType.BLOCK_IF_TRUE:
            return bool(value)
        if rule.rule_type == PipelineTaskQualityRuleType.BLOCK_IF_EQUALS:
            return value == rule.value
        if rule.rule_type == PipelineTaskQualityRuleType.BLOCK_IF_IN:
            return isinstance(rule.value, list) and value in rule.value
        return False
