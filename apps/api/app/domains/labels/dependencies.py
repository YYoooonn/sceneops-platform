from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.repositories import ArtifactRepositoryDep, LabelRunRepositoryDep
from app.domains.labels.service import LabelService


def get_label_service(
    repository: LabelRunRepositoryDep,
    artifact_repository: ArtifactRepositoryDep,
) -> LabelService:
    return LabelService(repository=repository, artifact_repository=artifact_repository)


LabelServiceDep = Annotated[LabelService, Depends(get_label_service)]
