from __future__ import annotations

from pathlib import Path

from sceneops_core.schemas.datasets import DatasetType
from sceneops_core.schemas.jobs import (
    IngestDatasetJobParams,
    IngestDatasetJobResult,
    JobManifest,
    JobType,
)
from sceneops_worker.ingest.nuscenes import ingest_nuscenes
from sceneops_worker.jobs.handlers.base import TypedJobHandler


class IngestDatasetJobHandler(
    TypedJobHandler[IngestDatasetJobParams, IngestDatasetJobResult]
):
    job_type = JobType.INGEST_DATASET

    def parse_params(self, job: JobManifest) -> IngestDatasetJobParams:
        return IngestDatasetJobParams.model_validate(job.params)

    def run(
        self,
        *,
        params: IngestDatasetJobParams,
        job: JobManifest,
    ) -> IngestDatasetJobResult:
        if params.dataset_type == DatasetType.NUSCENES:
            return self._run_nuscenes(params=params)

        raise ValueError(f"Unsupported dataset type: {params.dataset_type}")

    def _run_nuscenes(
        self,
        *,
        params: IngestDatasetJobParams,
    ) -> IngestDatasetJobResult:
        dataroot = (
            Path(params.raw_data_root)
            if params.raw_data_root is not None
            else self.context.raw_data_root
        )

        dataset_manifest = ingest_nuscenes(
            dataroot=dataroot,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            manifest_root=self.context.manifest_root,
            max_scenes=params.max_scenes,
            mode=params.mode.value,
        )

        dataset_manifest_uri = str(
            self.context.manifest_root
            / "datasets"
            / params.dataset_id
            / "versions"
            / params.dataset_version
            / "dataset.json"
        )

        return IngestDatasetJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            dataset_type=params.dataset_type,
            dataset_manifest_uri=dataset_manifest_uri,
            scene_count=dataset_manifest.summary.scene_count,
            sample_count=dataset_manifest.summary.sample_count,
            result_summary={
                "source": dataset_manifest.source,
                "status": dataset_manifest.status.value,
                "annotation_count": dataset_manifest.summary.annotation_count,
                "target_channels": dataset_manifest.channels.target or [],
            },
        )
