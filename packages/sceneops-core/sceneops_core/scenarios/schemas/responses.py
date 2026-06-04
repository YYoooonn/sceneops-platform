from __future__ import annotations

from sceneops_core.common.schemas import SceneOpsBaseModel

from .candidates import ScenarioCandidate
from .manifests import ScenarioMiningReport, ScenarioSetManifest
from .records import ScenarioRecord


class ScenarioDetailResponse(SceneOpsBaseModel):
    scenario: ScenarioRecord


class ScenarioListResponse(SceneOpsBaseModel):
    scenarios: list[ScenarioRecord]
    count: int


class ScenarioCandidateListResponse(SceneOpsBaseModel):
    candidates: list[ScenarioCandidate]
    count: int


class ScenarioSetDetailResponse(SceneOpsBaseModel):
    scenario_set: ScenarioSetManifest


class ScenarioMiningReportResponse(SceneOpsBaseModel):
    report: ScenarioMiningReport
