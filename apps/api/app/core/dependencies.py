from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_db.repositories import JobRepository, PostgresJobRepository
from sceneops_db.session import get_db_session

from app.config import (
    ApiSettings,
    MetadataBackend,
    StorageBackend,
    get_settings,
)
from app.modules.artifacts.gcs_storage import GcsArtifactStorage
from app.modules.artifacts.local_storage import LocalArtifactStorage
from app.modules.artifacts.s3_storage import S3ArtifactStorage
from app.modules.artifacts.service import ArtifactService
from app.modules.artifacts.storage import ArtifactStorage
from app.modules.datasets.local_repository import LocalManifestDatasetRepository
from app.modules.datasets.repository import DatasetRepository
from app.modules.datasets.service import DatasetService
from app.modules.evaluations.local_repository import LocalEvaluationRunRepository
from app.modules.evaluations.repository import EvaluationRunRepository
from app.modules.evaluations.service import EvaluationRunService
from app.modules.jobs.service import JobService
from app.modules.runs.local_repository import LocalInferenceRunRepository
from app.modules.runs.repository import InferenceRunRepository
from app.modules.runs.service import InferenceRunService


def get_dataset_repository(
    settings: ApiSettings = Depends(get_settings),
) -> DatasetRepository:
    if settings.metadata_backend == MetadataBackend.LOCAL_MANIFEST:
        return LocalManifestDatasetRepository(settings.manifest_root)

    if settings.metadata_backend == MetadataBackend.FIRESTORE:
        raise NotImplementedError("FireStore not implemented yet")

    raise ValueError(f"Unsupported metadata backend: {settings.metadata_backend}")


def get_artifact_storage(
    settings: ApiSettings = Depends(get_settings),
) -> ArtifactStorage:
    if settings.storage_backend == StorageBackend.LOCAL:
        return LocalArtifactStorage(settings.api_base_url)

    if settings.storage_backend == StorageBackend.GCS:
        if settings.gcs_bucket is None:
            raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
        return GcsArtifactStorage(settings.gcs_bucket)

    if settings.storage_backend == StorageBackend.S3:
        if settings.s3_bucket is None:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        return S3ArtifactStorage(settings.s3_bucket)

    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")


def get_dataset_service(
    repository: DatasetRepository = Depends(get_dataset_repository),
) -> DatasetService:
    return DatasetService(repository)


def get_artifact_service(
    dataset_repository: DatasetRepository = Depends(get_dataset_repository),
    artifact_storage: ArtifactStorage = Depends(get_artifact_storage),
) -> ArtifactService:
    return ArtifactService(
        dataset_repository=dataset_repository,
        artifact_storage=artifact_storage,
    )


def get_inference_run_repository(
    settings: ApiSettings = Depends(get_settings),
) -> InferenceRunRepository:
    return LocalInferenceRunRepository(settings.runs_root)


def get_inference_run_service(
    repository: InferenceRunRepository = Depends(get_inference_run_repository),
) -> InferenceRunService:
    return InferenceRunService(repository)


def get_evaluation_run_repository(
    settings: ApiSettings = Depends(get_settings),
) -> EvaluationRunRepository:
    return LocalEvaluationRunRepository(settings.runs_root)


def get_evaluation_run_service(
    repository: EvaluationRunRepository = Depends(get_evaluation_run_repository),
) -> EvaluationRunService:
    return EvaluationRunService(repository)


async def get_job_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobRepository:
    return PostgresJobRepository(session)


def get_job_service(
    settings: ApiSettings = Depends(get_settings),
    repository: JobRepository = Depends(get_job_repository),
) -> JobService:
    return JobService(
        repository=repository,
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )
