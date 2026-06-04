from __future__ import annotations

from pydantic import Field

from sceneops_core.common.schemas import JsonDict
from sceneops_core.scenarios.schemas import ScenarioCurationConfig

from .base import BaseJobParams


class MineScenariosJobParams(BaseJobParams):
    dataset_id: str
    dataset_version: str

    dataset_manifest_uri: str | None = None
    scene_ids: list[str] | None = None
    scene_manifest_uris: list[str] | None = None

    config: ScenarioCurationConfig = Field(default_factory=ScenarioCurationConfig)

    output_scenario_set_id: str | None = None

    metadata: JsonDict = Field(default_factory=dict)


class ScoreScenarioReadinessJobParams(BaseJobParams):
    scenario_set_id: str | None = None
    scenario_set_uri: str | None = None

    dataset_id: str | None = None
    dataset_version: str | None = None

    scoring_profile: str = "default"

    min_sensor_coverage: float | None = None
    min_annotation_coverage: float | None = None
    require_physics_validity: bool = False

    metadata: JsonDict = Field(default_factory=dict)
