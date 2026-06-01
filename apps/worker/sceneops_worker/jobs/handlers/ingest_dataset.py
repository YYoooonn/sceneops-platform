from __future__ import annotations

from sceneops_core.datasets.schemas import DatasetVersionStatus
from sceneops_core.jobs.schemas import (
    IngestDatasetJobParams,
    IngestDatasetJobResult,
    JobType,
)
from sceneops_worker.datasets.ingestion import (
    DatasetIngestionRequest,
    create_dataset_ingestor,
)
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class IngestDatasetJobHandler(
    JobHandler[IngestDatasetJobParams, IngestDatasetJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.INGEST_DATASET

    @property
    def params_model(self) -> type[IngestDatasetJobParams]:
        return IngestDatasetJobParams

    async def run(
        self,
        request: JobHandlerRequest[IngestDatasetJobParams],
    ) -> IngestDatasetJobResult:
        job = request.job
        params = request.params
        context = request.context

        registry = context.dataset_registry_store

        version = await registry.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )

        source_uri = params.source_uri or version.source_uri
        if source_uri is None:
            raise ValueError(
                f"source_uri is required for "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        await registry.upsert_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            dataset_type=version.dataset_type,
            source_uri=source_uri,
            manifest_uri=version.manifest_uri,
            scene_count=version.scene_count,
            sample_count=version.sample_count,
            annotation_count=version.annotation_count,
            status=DatasetVersionStatus.INGESTING,
            metadata=version.metadata,
        )

        ingestor = create_dataset_ingestor(params.dataset_type)

        try:
            dataset_manifest = await ingestor.run(
                DatasetIngestionRequest(
                    dataset_id=params.dataset_id,
                    dataset_version=params.dataset_version,
                    source_uri=source_uri,
                    dataset_artifact_store=context.dataset_artifact_store,
                    max_scenes=params.max_scenes,
                    mode=params.mode,
                )
            )

            await registry.upsert_version(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_type=dataset_manifest.dataset_type,
                source_uri=dataset_manifest.uris.raw_root,
                manifest_uri=dataset_manifest.uris.dataset_manifest,
                scene_count=dataset_manifest.summary.scene_count,
                sample_count=dataset_manifest.summary.sample_count,
                annotation_count=dataset_manifest.summary.annotation_count,
                status=DatasetVersionStatus.INGESTED,
                metadata={
                    **(version.metadata or {}),
                    "last_ingest_job_id": job.job_id,
                    "ingestor_type": ingestor.dataset_type,
                    "source": dataset_manifest.source,
                    "target_channels": dataset_manifest.channels.target,
                },
            )

            return IngestDatasetJobResult(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_type=params.dataset_type,
                dataset_manifest_uri=dataset_manifest.uris.dataset_manifest,
                scene_count=dataset_manifest.summary.scene_count,
                sample_count=dataset_manifest.summary.sample_count,
                result_summary={
                    "source": dataset_manifest.source,
                    "status": dataset_manifest.status.value,
                    "annotation_count": dataset_manifest.summary.annotation_count,
                    "target_channels": dataset_manifest.channels.target,
                },
            )

        except Exception:
            await registry.upsert_version(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_type=version.dataset_type,
                source_uri=source_uri,
                manifest_uri=version.manifest_uri,
                scene_count=version.scene_count,
                sample_count=version.sample_count,
                annotation_count=version.annotation_count,
                status=DatasetVersionStatus.FAILED,
                metadata={
                    **(version.metadata or {}),
                    "last_failed_ingest_job_id": job.job_id,
                },
            )
            raise
