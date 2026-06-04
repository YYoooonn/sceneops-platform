from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class MineScenariosJobResult(BaseJobResult):
    scenario_set_id: str

    scenario_set_uri: str | None = None
    report_uri: str | None = None

    candidate_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0

    metadata: JsonDict = Field(default_factory=dict)


class ScoreScenarioReadinessJobResult(BaseJobResult):
    scenario_set_id: str | None = None

    readiness_report_uri: str | None = None

    average_score: float | None = None
    ready_count: int = 0
    blocked_count: int = 0

    summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)
