from __future__ import annotations

import json
from typing import Any

from sceneops_core.common.checksums import sha256_checksum
from sceneops_storage import ArtifactStore


class RunArtifactConflictError(RuntimeError):
    """A write-once run artifact key already holds different bytes."""


class RunArtifactIntegrityError(RuntimeError):
    """Stored run artifact bytes do not match their pinned checksum."""


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
    #
    # A run's prediction manifest revision lives in the DerivedManifestStore
    # (checksum-qualified, write-once); only its per-sample shards, which
    # the manifest pins by checksum, are written here.

    def inference_run_root_uri(self, run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.runs_root_uri,
            "inference",
            run_id,
        )

    def inference_predictions_root_uri(self, run_id: str) -> str:
        return self.artifact_store.join_uri(
            self.inference_run_root_uri(run_id),
            "predictions",
        )

    def prediction_shard_uri(
        self, *, run_id: str, scene_id: str, sample_id: str
    ) -> str:
        # A sample id is unique only inside its Scene's view.
        return self.artifact_store.join_uri(
            self.inference_predictions_root_uri(run_id),
            scene_id,
            f"{sample_id}.json",
        )

    async def write_prediction_shard(
        self, *, run_id: str, scene_id: str, sample_id: str, data: bytes
    ) -> str:
        """Write-once: the same bytes are a retry, different bytes a conflict."""
        uri = self.prediction_shard_uri(
            run_id=run_id, scene_id=scene_id, sample_id=sample_id
        )
        if await self.artifact_store.exists(uri):
            if await self.artifact_store.read_bytes(uri) != data:
                raise RunArtifactConflictError(
                    f"{uri} already holds different bytes; prediction shards are write-once"
                )
            return uri
        await self.artifact_store.write_bytes(uri, data)
        return uri

    async def read_pinned_prediction_shard(
        self, *, uri: str, checksum: str
    ) -> dict[str, Any]:
        """A prediction shard, verified against the checksum its manifest pins."""
        data = await self.artifact_store.read_bytes(uri)
        if sha256_checksum(data) != checksum:
            raise RunArtifactIntegrityError(
                f"prediction shard {uri} does not match its pinned checksum {checksum}"
            )
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise RunArtifactIntegrityError(f"invalid prediction shard: {uri}")
        return payload

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
        scene_id: str,
        sample_id: str,
    ) -> str:
        # A sample id is unique only inside its Scene's view.
        return self.artifact_store.join_uri(
            self.evaluation_samples_root_uri(evaluation_run_id),
            scene_id,
            f"{sample_id}.json",
        )

    async def write_sample_evaluation_manifest(
        self,
        *,
        evaluation_run_id: str,
        scene_id: str,
        sample_id: str,
        manifest: dict[str, Any],
    ) -> str:
        uri = self.sample_evaluation_manifest_uri(
            evaluation_run_id=evaluation_run_id,
            scene_id=scene_id,
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
