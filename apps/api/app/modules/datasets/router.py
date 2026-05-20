from fastapi import APIRouter, Depends

from app.core.dependencies import get_dataset_service
from app.modules.datasets.schemas import (
    DatasetIndexItem,
    DatasetVersionManifest,
    SampleManifest,
    SceneIndexItem,
    SceneManifest,
)
from app.modules.datasets.service import DatasetService
from app.shared.errors import not_found

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[DatasetIndexItem])
def list_datasets(
    service: DatasetService = Depends(get_dataset_service),
):
    return service.list_datasets()


@router.get(
    "/{dataset_id}/versions/{dataset_version}",
    response_model=DatasetVersionManifest,
)
def get_dataset_version(
    dataset_id: str,
    dataset_version: str,
    service: DatasetService = Depends(get_dataset_service),
):
    dataset = service.get_dataset_version(dataset_id, dataset_version)

    if dataset is None:
        raise not_found("Dataset version not found")

    return dataset


@router.get(
    "/{dataset_id}/versions/{dataset_version}/scenes",
    response_model=list[SceneIndexItem],
)
def list_scenes(
    dataset_id: str,
    dataset_version: str,
    service: DatasetService = Depends(get_dataset_service),
):
    dataset = service.get_dataset_version(dataset_id, dataset_version)

    if dataset is None:
        raise not_found("Dataset version not found")

    return service.list_scenes(dataset_id, dataset_version)


@router.get(
    "/{dataset_id}/versions/{dataset_version}/scenes/{scene_id}",
    response_model=SceneManifest,
)
def get_scene(
    dataset_id: str,
    dataset_version: str,
    scene_id: str,
    service: DatasetService = Depends(get_dataset_service),
):
    scene = service.get_scene(dataset_id, dataset_version, scene_id)

    if scene is None:
        raise not_found("Scene not found")

    return scene


@router.get(
    "/{dataset_id}/versions/{dataset_version}/scenes/{scene_id}/samples",
    response_model=list[SampleManifest],
)
def list_samples_by_scene(
    dataset_id: str,
    dataset_version: str,
    scene_id: str,
    service: DatasetService = Depends(get_dataset_service),
):
    scene = service.get_scene(dataset_id, dataset_version, scene_id)

    if scene is None:
        raise not_found("Scene not found")

    return service.list_samples_by_scene(dataset_id, dataset_version, scene_id)


@router.get(
    "/{dataset_id}/versions/{dataset_version}/samples/{sample_id}",
    response_model=SampleManifest,
)
def get_sample(
    dataset_id: str,
    dataset_version: str,
    sample_id: str,
    service: DatasetService = Depends(get_dataset_service),
):
    sample = service.get_sample(dataset_id, dataset_version, sample_id)

    if sample is None:
        raise not_found("Sample not found")

    return sample
