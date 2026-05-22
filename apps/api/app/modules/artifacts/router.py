from fastapi import APIRouter, Depends

from sceneops_core.schemas.artifacts import SampleArtifact

from app.core.dependencies import get_artifact_service
from app.modules.artifacts.service import ArtifactService
from app.shared.errors import not_found

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get(
    "/datasets/{dataset_id}/versions/{dataset_version}/samples/{sample_id}",
    response_model=list[SampleArtifact],
)
def list_sample_artifacts(
    dataset_id: str,
    dataset_version: str,
    sample_id: str,
    service: ArtifactService = Depends(get_artifact_service),
):
    artifacts = service.list_sample_artifacts(
        dataset_id,
        dataset_version,
        sample_id,
    )

    if not artifacts:
        raise not_found("Sample artifacts not found")

    return artifacts
