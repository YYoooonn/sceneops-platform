from __future__ import annotations

from typing import Any

from sceneops_core.inference.schemas.manifests import DetectionPredictionManifest
from sceneops_storage import ArtifactStore


class RunArtifactStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        runs_root_uri: str,
    ) -> None:
        self.artifact_store = artifact_store
        self.runs_root_uri = runs_root_uri

    # ---------------------------------------------------------------------
    # Inference runs
    # ---------------------------------------------------------------------

    def inference_run_root_uri(self, run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.runs_root_uri,
            "inference",
            run_id,
        )

    def inference_run_manifest_uri(self, run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.inference_run_root_uri(run_id),
            "run.json",
        )

    def inference_prediction_manifest_uri(self, run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.inference_run_root_uri(run_id),
            "prediction_manifest.json",
        )

    def inference_predictions_root_uri(self, run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.inference_run_root_uri(run_id),
            "predictions",
        )

    def prediction_manifest_uri(
        self,
        *,
        run_id: str,
        sample_id: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.inference_predictions_root_uri(run_id),
            f"{sample_id}.json",
        )

    async def write_inference_run_manifest(
        self,
        *,
        run_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.inference_run_manifest_uri(run_id)
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def load_inference_run_manifest(
        self,
        *,
        run_id: str,
    ) -> DetectionPredictionManifest:
        uri = self.inference_run_manifest_uri(run_id)
        raw = await self.artifact_store.read_json(uri)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid inference run manifest: {uri}")

        return DetectionPredictionManifest.model_validate(raw)

    async def load_inference_prediction_manifest(
        self,
        *,
        run_id: str,
    ) -> DetectionPredictionManifest:
        uri = self.inference_prediction_manifest_uri(run_id)
        payload = await self.artifact_store.read_json(uri)
        return DetectionPredictionManifest.model_validate(payload)

    async def write_sample_prediction_manifest(
        self,
        *,
        run_id: str,
        sample_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.prediction_manifest_uri(
            run_id=run_id,
            sample_id=sample_id,
        )
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def write_inference_prediction_manifest(
        self,
        *,
        run_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.inference_prediction_manifest_uri(run_id)
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def list_prediction_manifest_uris(
        self,
        *,
        run_id: str,
    ) -> list[str]:
        return await self.artifact_store.list_json(
            self.inference_predictions_root_uri(run_id)
        )

    async def load_sample_prediction_manifest(
        self,
        *,
        uri: str,
    ) -> dict[str, Any]:
        raw = await self.artifact_store.read_json(uri)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid prediction manifest: {uri}")

        return raw

    # ---------------------------------------------------------------------
    # Dataset validation runs
    # ---------------------------------------------------------------------

    def validation_run_root_uri(self, validation_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.runs_root_uri,
            "dataset_validations",
            validation_run_id,
        )

    def validation_run_manifest_uri(self, validation_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.validation_run_root_uri(validation_run_id),
            "validation_report.json",
        )

    async def write_validation_run_manifest(
        self,
        *,
        validation_run_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.validation_run_manifest_uri(validation_run_id)
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def load_validation_run_manifest(
        self,
        *,
        validation_run_id: str,
    ) -> dict[str, Any]:
        uri = self.validation_run_manifest_uri(validation_run_id)
        raw = await self.artifact_store.read_json(uri)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid validation run manifest: {uri}")

        return raw

    async def load_validation_run_manifest_by_uri(
        self,
        *,
        uri: str,
    ) -> dict[str, Any]:
        raw = await self.artifact_store.read_json(uri)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid validation run manifest: {uri}")

        return raw

    # ---------------------------------------------------------------------
    # Dataset profile runs
    # ---------------------------------------------------------------------

    def dataset_profile_run_root_uri(self, profile_run_id: str):
        return self.artifact_store.join_uri(
            self.runs_root_uri,
            "dataset_profiles",
            profile_run_id,
        )

    def dataset_profile_run_report_uri(self, profile_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.dataset_profile_run_root_uri(profile_run_id),
            "profile_report.json",
        )

    async def write_dataset_profile_run_report(
        self,
        *,
        profile_run_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.dataset_profile_run_report_uri(profile_run_id=profile_run_id)
        await self.artifact_store.write_json(uri, manifest)
        return uri

    # ---------------------------------------------------------------------
    # Evaluation runs
    # ---------------------------------------------------------------------

    def evaluation_run_root_uri(self, evaluation_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.runs_root_uri,
            "evaluations",
            evaluation_run_id,
        )

    def evaluation_run_manifest_uri(self, evaluation_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.evaluation_run_root_uri(evaluation_run_id),
            "evaluation.json",
        )

    def evaluation_run_metrics_uri(self, evaluation_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.evaluation_run_root_uri(evaluation_run_id),
            "metrics.json",
        )

    def evaluation_samples_root_uri(self, evaluation_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.evaluation_run_root_uri(evaluation_run_id),
            "samples",
        )

    def sample_evaluation_manifest_uri(
        self,
        *,
        evaluation_run_id: str,
        sample_id: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.evaluation_samples_root_uri(evaluation_run_id),
            f"{sample_id}.json",
        )

    async def write_sample_evaluation_manifest(
        self,
        *,
        evaluation_run_id: str,
        sample_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.sample_evaluation_manifest_uri(
            evaluation_run_id=evaluation_run_id,
            sample_id=sample_id,
        )
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def write_evaluation_run_manifest(
        self,
        *,
        evaluation_run_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.evaluation_run_manifest_uri(evaluation_run_id)
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def write_evaluation_run_metrics(
        self,
        *,
        evaluation_run_id: str,
        metrics: dict[str, Any],
    ) -> str:
        uri = self.evaluation_run_metrics_uri(evaluation_run_id)
        await self.artifact_store.write_json(uri, metrics)
        return uri

    # ---------------------------------------------------------------------
    # Auto-label runs
    # ---------------------------------------------------------------------

    def auto_label_run_root_uri(self, auto_label_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.runs_root_uri,
            "auto_labels",
            auto_label_run_id,
        )

    def auto_label_run_manifest_uri(self, auto_label_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.auto_label_run_root_uri(auto_label_run_id),
            "auto_label.json",
        )

    def auto_label_samples_root_uri(self, auto_label_run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.auto_label_run_root_uri(auto_label_run_id),
            "samples",
        )

    def auto_label_sample_manifest_uri(
        self,
        *,
        auto_label_run_id: str,
        sample_id: str,
    ) -> str:
        return self.artifact_store.join_uri(
            self.auto_label_samples_root_uri(auto_label_run_id),
            f"{sample_id}.json",
        )

    async def write_auto_label_run_manifest(
        self,
        *,
        auto_label_run_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.auto_label_run_manifest_uri(auto_label_run_id)
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def load_auto_label_run_manifest(
        self,
        *,
        auto_label_run_id: str,
    ) -> dict[str, Any]:
        uri = self.auto_label_run_manifest_uri(auto_label_run_id)
        raw = await self.artifact_store.read_json(uri)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid auto-label run manifest: {uri}")

        return raw

    async def write_auto_label_sample_manifest(
        self,
        *,
        auto_label_run_id: str,
        sample_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.auto_label_sample_manifest_uri(
            auto_label_run_id=auto_label_run_id,
            sample_id=sample_id,
        )
        await self.artifact_store.write_json(uri, manifest)
        return uri

    async def list_auto_label_sample_manifest_uris(
        self,
        *,
        auto_label_run_id: str,
    ) -> list[str]:
        return await self.artifact_store.list_json(
            self.auto_label_samples_root_uri(auto_label_run_id)
        )
