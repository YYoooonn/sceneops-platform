from __future__ import annotations

from sceneops_core.common.schemas import JsonDict
from sceneops_db.datasets import DatasetVersionRepository
from sceneops_db.runs import (
    DatasetValidationRunRepository,
    EvaluationRunRepository,
    InferenceRunRepository,
)

from sceneops_storage import ArtifactStore


class ArtifactService:
    def __init__(
        self,
        *,
        dataset_version_repository: DatasetVersionRepository,
        inference_run_repository: InferenceRunRepository,
        evaluation_run_repository: EvaluationRunRepository,
        validation_run_repository: DatasetValidationRunRepository,
        artifact_store: ArtifactStore,
    ) -> None:
        self.dataset_version_repository = dataset_version_repository
        self.inference_run_repository = inference_run_repository
        self.evaluation_run_repository = evaluation_run_repository
        self.validation_run_repository = validation_run_repository
        self.artifact_store = artifact_store

    async def get_dataset_manifest(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
    ) -> JsonDict:
        version = await self.dataset_version_repository.get(
            dataset_id=dataset_id,
            version=dataset_version,
        )

        if version.manifest_uri is None:
            raise FileNotFoundError(
                f"Dataset manifest not found: {dataset_id}:{dataset_version}"
            )

        artifact = await self.artifact_store.read_json(version.manifest_uri)
        if not isinstance(artifact, dict):
            raise ValueError(f"Invalid dataset manifest: {version.manifest_uri}")

        return artifact

    async def get_inference_run_manifest(self, run_id: str) -> JsonDict:
        run = await self.inference_run_repository.get(run_id)

        if run.run_manifest_uri is None:
            raise FileNotFoundError(f"Inference run manifest not found: {run_id}")

        artifact = await self.artifact_store.read_json(run.run_manifest_uri)
        if not isinstance(artifact, dict):
            raise ValueError(f"Invalid inference run manifest: {run.run_manifest_uri}")

        return artifact

    async def get_evaluation_run_manifest(
        self,
        evaluation_run_id: str,
    ) -> JsonDict:
        run = await self.evaluation_run_repository.get(evaluation_run_id)

        if run.evaluation_manifest_uri is None:
            raise FileNotFoundError(
                f"Evaluation run manifest not found: {evaluation_run_id}"
            )

        artifact = await self.artifact_store.read_json(run.evaluation_manifest_uri)
        if not isinstance(artifact, dict):
            raise ValueError(
                f"Invalid evaluation run manifest: {run.evaluation_manifest_uri}"
            )

        return artifact

    async def get_validation_run_report(
        self,
        validation_run_id: str,
    ) -> JsonDict:
        run = await self.validation_run_repository.get(validation_run_id)

        if run.validation_report_uri is None:
            raise FileNotFoundError(
                f"validation run manifest not found: {validation_run_id}"
            )

        artifact = await self.artifact_store.read_json(run.validation_report_uri)
        if not isinstance(artifact, dict):
            raise ValueError(
                f"Invalid validation run report: {run.validation_report_uri}"
            )

        return artifact
