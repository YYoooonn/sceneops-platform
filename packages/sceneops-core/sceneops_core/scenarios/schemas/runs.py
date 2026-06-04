from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.runs.schemas import BaseRunRecord, RunType


class ScenarioMiningRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENARIO_MINING

    dataset_id: str | None = None
    dataset_version: str | None = None
    dataset_manifest_uri: str | None = None

    scenario_set_id: str | None = None
    scenario_set_uri: str | None = None

    mining_report_uri: str | None = None

    candidate_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0

    predicate_summary: JsonDict = Field(default_factory=dict)
    score_summary: JsonDict = Field(default_factory=dict)


class ScenarioReadinessRunRecord(BaseRunRecord):
    type: RunType = RunType.SCENARIO_READINESS

    scenario_set_id: str | None = None
    scenario_set_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    readiness_report_uri: str | None = None

    scenario_count: int | None = None
    ready_count: int = 0
    blocked_count: int = 0
    warning_count: int = 0

    average_score: float | None = None
    min_score: float | None = None
    max_score: float | None = None

    summary: JsonDict = Field(default_factory=dict)
