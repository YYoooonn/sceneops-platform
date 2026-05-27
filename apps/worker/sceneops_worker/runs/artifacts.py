from __future__ import annotations

from pathlib import Path
from typing import Any

from sceneops_core.paths.runs import (
    evaluation_run_manifest_path,
    evaluation_run_root,
    inference_run_manifest_path,
    inference_run_root,
    prediction_manifest_path,
    sample_evaluation_manifest_path,
)
from sceneops_worker.storage import ArtifactStore


class RunArtifactStore:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        runs_root: Path,
    ) -> None:
        self.artifact_store = artifact_store
        self.runs_root = runs_root

    def inference_run_manifest_uri(self, run_id: str) -> str:
        return str(
            inference_run_manifest_path(
                runs_root=self.runs_root,
                run_id=run_id,
            )
        )

    def inference_predictions_root_uri(self, run_id: str) -> str:
        return str(
            inference_run_root(
                runs_root=self.runs_root,
                run_id=run_id,
            )
            / "predictions"
        )

    def prediction_manifest_uri(
        self,
        *,
        run_id: str,
        sample_id: str,
    ) -> str:
        return str(
            prediction_manifest_path(
                runs_root=self.runs_root,
                run_id=run_id,
                sample_id=sample_id,
            )
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
    ) -> dict[str, Any]:
        uri = self.inference_run_manifest_uri(run_id)
        raw = await self.artifact_store.read_json(uri)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid inference run manifest: {uri}")

        return raw

    async def write_prediction_manifest(
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

    async def list_prediction_manifest_uris(
        self,
        *,
        run_id: str,
    ) -> list[str]:
        return await self.artifact_store.list_json(
            self.inference_predictions_root_uri(run_id)
        )

    async def load_prediction_manifest(
        self,
        *,
        uri: str,
    ) -> dict[str, Any]:
        raw = await self.artifact_store.read_json(uri)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid prediction manifest: {uri}")

        return raw

    def evaluation_run_manifest_uri(self, evaluation_run_id: str) -> str:
        return str(
            evaluation_run_manifest_path(
                runs_root=self.runs_root,
                evaluation_run_id=evaluation_run_id,
            )
        )

    def evaluation_samples_root_uri(self, evaluation_run_id: str) -> str:
        return str(
            evaluation_run_root(
                runs_root=self.runs_root,
                evaluation_run_id=evaluation_run_id,
            )
            / "samples"
        )

    def sample_evaluation_manifest_uri(
        self,
        *,
        evaluation_run_id: str,
        sample_id: str,
    ) -> str:
        return str(
            sample_evaluation_manifest_path(
                runs_root=self.runs_root,
                evaluation_run_id=evaluation_run_id,
                sample_id=sample_id,
            )
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
