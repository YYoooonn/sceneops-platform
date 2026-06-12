from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict

from .base import BaseJobResult


class MineScenariosJobResult(BaseJobResult):
    scenario_set_id: str

    scenario_set_uri: str | None = None
    report_uri: str | None = (
        None  # scenario candidates JSON (= mining_report_uri in run record)
    )
    mining_run_id: str | None = None

    candidate_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0

    selected_scene_ids: list[str] = Field(default_factory=list)
    summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)


class ScoreScenarioReadinessJobResult(BaseJobResult):
    scenario_set_id: str | None = None

    readiness_report_uri: str | None = None
    readiness_run_id: str | None = None

    scored_scene_count: int = 0
    average_score: float | None = None
    ready_count: int = 0
    warning_count: int = 0
    blocked_count: int = 0

    top_scene_ids: list[str] = Field(default_factory=list)
    summary: JsonDict = Field(default_factory=dict)

    metadata: JsonDict = Field(default_factory=dict)
