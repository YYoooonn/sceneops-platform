"""Loading helpers for detection evaluation.

These functions are evaluator-algorithm-agnostic. Any detection evaluator
(center-distance, IoU-3D, nuScenes metric, …) can use them.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.inference.schemas.manifests import DetectionPredictionManifest
from sceneops_core.scenes.schemas.manifests import SceneSampleManifest
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore


async def load_prediction_manifest(
    request: DetectionEvaluationRequest,
) -> DetectionPredictionManifest:
    """Load the run-level prediction_manifest.json for an inference run.

    Reads:
      runs/inference/{inference_run_id}/prediction_manifest.json

    Does NOT read run.json (runs/inference/{inference_run_id}/run.json).
    """
    return await request.run_artifact_store.load_inference_prediction_manifest(
        run_id=request.inference_run_id
    )


async def build_sample_index(
    *,
    dataset_manifest: DatasetManifest,
    scene_artifact_store: SceneArtifactStore,
) -> dict[str, SceneSampleManifest]:
    """Build a sample_id → SceneSampleManifest lookup from all scenes in the manifest."""
    sample_index: dict[str, SceneSampleManifest] = {}

    for scene_entry in dataset_manifest.scenes:
        scene_manifest = await scene_artifact_store.load_scene_manifest(
            scene_entry.scene_manifest_uri
        )
        if scene_manifest is None:
            continue
        for sample in scene_manifest.samples:
            sample_index[sample.sample_id] = sample

    return sample_index


async def load_sample_prediction_payload(
    *,
    run_artifact_store: RunArtifactStore,
    uri: str,
) -> dict[str, Any]:
    """Load a per-sample prediction file as a raw dict.

    Sample prediction files live at:
      runs/inference/{inference_run_id}/predictions/{sample_id}.json

    These are NOT parsed as DetectionPredictionManifest — they are plain dicts
    with keys: run_id, sample_id, predictions, …
    """
    payload = await run_artifact_store.load_sample_prediction_manifest(uri=uri)

    if "sample_id" not in payload:
        raise ValueError(f"Sample prediction payload missing 'sample_id': {uri!r}")

    if "predictions" not in payload:
        raise ValueError(f"Sample prediction payload missing 'predictions': {uri!r}")

    return payload
