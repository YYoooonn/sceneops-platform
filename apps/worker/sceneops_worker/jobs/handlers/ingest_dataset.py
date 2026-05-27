from __future__ import annotations

from pathlib import Path

from sceneops_core.schemas.datasets import DatasetType, DatasetVersionStatus
from sceneops_core.schemas.jobs import (
    IngestDatasetJobParams,
    IngestDatasetJobResult,
    JobManifest,
    JobType,
)
from sceneops_worker.datasets.nuscenes import ingest_nuscenes
from sceneops_worker.jobs.handlers.base import TypedJobHandler


class IngestDatasetJobHandler(
    TypedJobHandler[IngestDatasetJobParams, IngestDatasetJobResult]
):
    job_type = JobType.INGEST_DATASET

    def parse_params(self, job: JobManifest) -> IngestDatasetJobParams:
        return IngestDatasetJobParams.model_validate(job.params)

    async def run(
        self,
        *,
        params: IngestDatasetJobParams,
        job: JobManifest,
    ) -> IngestDatasetJobResult:
        if params.dataset_type == DatasetType.NUSCENES:
            return await self._run_nuscenes(params=params, job=job)

        raise ValueError(f"Unsupported dataset type: {params.dataset_type}")

    async def _run_nuscenes(
        self,
        *,
        params: IngestDatasetJobParams,
        job: JobManifest,
    ) -> IngestDatasetJobResult:
        registry = self.context.dataset_registry_store

        version = await registry.get_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )

        raw_data_uri = params.raw_data_root or version.raw_data_uri
        if raw_data_uri is None:
            raise ValueError(
                f"raw_data_uri is required for "
                f"{params.dataset_id}:{params.dataset_version}"
            )

        await registry.upsert_version(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            dataset_type=version.dataset_type,
            raw_data_uri=raw_data_uri,
            manifest_uri=version.manifest_uri,
            scene_count=version.scene_count,
            sample_count=version.sample_count,
            annotation_count=version.annotation_count,
            status=DatasetVersionStatus.INGESTING,
            metadata=version.metadata,
        )

        try:
            dataset_manifest = await ingest_nuscenes(
                raw_data_root=Path(raw_data_uri),
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                manifest_root_uri=str(self.context.manifest_root),
                dataset_artifact_store=self.context.dataset_artifact_store,
                max_scenes=params.max_scenes,
                mode=params.mode.value,
            )

            await registry.upsert_version(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                dataset_type=dataset_manifest.dataset_type,
                raw_data_uri=dataset_manifest.uris.raw_root,
                manifest_uri=dataset_manifest.uris.dataset_manifest,
                scene_count=dataset_manifest.summary.scene_count,
                sample_count=dataset_manifest.summary.sample_count,
                annotation_count=dataset_manifest.summary.annotation_count,
                status=DatasetVersionStatus.INGESTED,
                metadata={
                    **(version.metadata or {}),
                    "last_ingest_job_id": job.job_id,
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
                raw_data_uri=raw_data_uri,
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
