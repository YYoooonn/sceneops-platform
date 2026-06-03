from __future__ import annotations

from typing import Any

from sceneops_core.common.schemas import JsonDict
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
from sceneops_worker.pipelines.context_keys import PipelineContextKey as Ctx


class IngestDatasetJobHandler(
    JobHandler[IngestDatasetJobParams, IngestDatasetJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.INGEST_DATASET

    @property
    def params_model(self) -> type[IngestDatasetJobParams]:
        return IngestDatasetJobParams

    def build_step_params(
        self, base: JsonDict, context_values: dict[str, Any]
    ) -> JsonDict:
        return {
            "dataset_type": base.get("dataset_type", "nuscenes"),
            **base,
        }

    def extract_context_updates(self, result: JsonDict) -> dict[str, Any]:
        parsed = IngestDatasetJobResult.model_validate(result)
        updates: dict[str, Any] = {
            Ctx.DATASET_ID: parsed.dataset_id,
            Ctx.DATASET_VERSION: parsed.dataset_version,
            Ctx.DATASET_MANIFEST_URI: parsed.dataset_manifest_uri,
            Ctx.SCENE_COUNT: parsed.scene_count,
            Ctx.SAMPLE_COUNT: parsed.sample_count,
        }
        if parsed.dataset_type is not None:
            updates[Ctx.DATASET_TYPE] = _enum_or_value(parsed.dataset_type)
        return updates

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


def _enum_or_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value
