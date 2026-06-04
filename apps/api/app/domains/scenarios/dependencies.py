from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import ArtifactRepositoryDep, ScenarioSetRepositoryDep
from app.domains.scenarios.service import ScenarioService


def get_scenario_service(
    repository: ScenarioSetRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
) -> ScenarioService:
    return ScenarioService(
        repository=repository, artifact_repository=artifact_repository
    )


ScenarioServiceDep = Annotated[ScenarioService, Depends(get_scenario_service)]
