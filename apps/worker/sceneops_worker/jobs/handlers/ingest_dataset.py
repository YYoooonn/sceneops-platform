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
        if params.datasetType == DatasetType.NUSCENES:
            return self._run_nuscenes(params=params)

        raise ValueError(f"Unsupported dataset type: {params.datasetType}")

    def _run_nuscenes(
        self,
        *,
        params: IngestDatasetJobParams,
    ) -> IngestDatasetJobResult:
        dataroot = (
            Path(params.rawDataRoot)
            if params.rawDataRoot is not None
            else self.context.raw_data_root
        )

        dataset_manifest = ingest_nuscenes(
            dataroot=dataroot,
            dataset_id=params.datasetId,
            dataset_version=params.datasetVersion,
            manifest_root=self.context.manifest_root,
            max_scenes=params.maxScenes,
            mode=params.mode.value,
        )

        manifest_uri = str(
            self.context.manifest_root
            / "datasets"
            / params.datasetId
            / "versions"
            / params.datasetVersion
            / "dataset.json"
        )

        return IngestDatasetJobResult(
            datasetId=params.datasetId,
            datasetVersion=params.datasetVersion,
            datasetType=params.datasetType,
            manifestUri=manifest_uri,
            sceneCount=int(dataset_manifest.get("sceneCount", 0)),
            sampleCount=int(dataset_manifest.get("sampleCount", 0)),
            resultSummary={
                "source": dataset_manifest.get("source"),
                "status": dataset_manifest.get("status"),
                "annotationCount": dataset_manifest.get("annotationCount", 0),
                "targetChannels": dataset_manifest.get("targetChannels", []),
            },
        )
