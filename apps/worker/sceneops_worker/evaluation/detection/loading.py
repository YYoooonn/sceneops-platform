from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.inference.schemas.manifests import DetectionPredictionManifest
from sceneops_core.scenes.schemas.manifests import (
    SceneManifest,
    SceneSampleManifest,
)
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore


@dataclass(frozen=True)
class EvaluationSceneEntry:
    scene_id: str
    scene_manifest_uri: str
    manifest: SceneManifest

    sample_count: int
    frame_count: int
    annotation_count: int

    has_ground_truth: bool
    ground_truth_source: str | None = None


@dataclass(frozen=True)
class EvaluationSceneIndex:
    scenes: list[EvaluationSceneEntry] = field(default_factory=list)
    scenes_by_id: dict[str, EvaluationSceneEntry] = field(default_factory=dict)

    samples_by_id: dict[str, SceneSampleManifest] = field(default_factory=dict)
    scene_by_sample_id: dict[str, EvaluationSceneEntry] = field(default_factory=dict)

    scene_count: int = 0
    sample_count: int = 0
    frame_count: int = 0
    annotation_count: int = 0
    ground_truth_scene_count: int = 0

    def get_sample(self, sample_id: str) -> SceneSampleManifest | None:
        return self.samples_by_id.get(sample_id)

    def get_scene_for_sample(self, sample_id: str) -> EvaluationSceneEntry | None:
        return self.scene_by_sample_id.get(sample_id)

    def get_scene(self, scene_id: str | None) -> EvaluationSceneEntry | None:
        if scene_id is None:
            return None
        return self.scenes_by_id.get(scene_id)


async def load_prediction_manifest(
    request: DetectionEvaluationRequest,
) -> DetectionPredictionManifest:
    return await request.run_artifact_store.load_inference_prediction_manifest(
        run_id=request.inference_run_id
    )


async def build_scene_index(
    *,
    dataset_manifest: DatasetManifest,
    scene_artifact_store: SceneArtifactStore,
) -> EvaluationSceneIndex:
    scenes: list[EvaluationSceneEntry] = []
    scenes_by_id: dict[str, EvaluationSceneEntry] = {}

    samples_by_id: dict[str, SceneSampleManifest] = {}
    scene_by_sample_id: dict[str, EvaluationSceneEntry] = {}

    total_sample_count = 0
    total_frame_count = 0
    total_annotation_count = 0
    ground_truth_scene_count = 0

    for scene_entry in dataset_manifest.scenes:
        scene_manifest = await scene_artifact_store.load_scene_manifest(
            scene_entry.scene_manifest_uri
        )
        if scene_manifest is None:
            continue

        annotation_count = int(scene_manifest.annotation_count or 0)
        sample_count = int(scene_manifest.sample_count or len(scene_manifest.samples))
        frame_count = int(
            scene_manifest.frame_count
            or sum(len(sample.sensor_frames) for sample in scene_manifest.samples)
        )

        has_ground_truth = bool(scene_manifest.has_ground_truth) or annotation_count > 0

        loaded_scene = EvaluationSceneEntry(
            scene_id=scene_manifest.scene_id,
            scene_manifest_uri=scene_entry.scene_manifest_uri,
            manifest=scene_manifest,
            sample_count=sample_count,
            frame_count=frame_count,
            annotation_count=annotation_count,
            has_ground_truth=has_ground_truth,
            ground_truth_source=scene_manifest.ground_truth_source,
        )

        scenes.append(loaded_scene)
        scenes_by_id[loaded_scene.scene_id] = loaded_scene

        total_sample_count += sample_count
        total_frame_count += frame_count
        total_annotation_count += annotation_count

        if has_ground_truth:
            ground_truth_scene_count += 1

        for sample in scene_manifest.samples:
            samples_by_id[sample.sample_id] = sample
            scene_by_sample_id[sample.sample_id] = loaded_scene

    return EvaluationSceneIndex(
        scenes=scenes,
        scenes_by_id=scenes_by_id,
        samples_by_id=samples_by_id,
        scene_by_sample_id=scene_by_sample_id,
        scene_count=len(scenes),
        sample_count=total_sample_count,
        frame_count=total_frame_count,
        annotation_count=total_annotation_count,
        ground_truth_scene_count=ground_truth_scene_count,
    )


async def load_sample_prediction_payload(
    *,
    run_artifact_store: RunArtifactStore,
    uri: str,
) -> dict[str, Any]:
    payload = await run_artifact_store.load_sample_prediction_manifest(uri=uri)

    if "sample_id" not in payload:
        raise ValueError(f"Sample prediction payload missing 'sample_id': {uri!r}")

    if "predictions" not in payload:
        raise ValueError(f"Sample prediction payload missing 'predictions': {uri!r}")

    return payload
