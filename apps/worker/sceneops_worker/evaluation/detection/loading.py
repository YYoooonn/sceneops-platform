from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sceneops_core.datasets.schemas import DatasetManifest
from sceneops_core.inference.schemas.manifests import DetectionPredictionManifest
from sceneops_core.scenes.schemas import SceneManifest
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from sceneops_worker.runs import RunArtifactStore
from sceneops_worker.scenes import SceneArtifactStore
from sceneops_worker.scenes.keyframes import (
    KeyframeSample,
    keyframe_samples,
    load_pinned_scene,
)


@dataclass(frozen=True)
class EvaluationSceneEntry:
    scene_id: str
    manifest: SceneManifest

    keyframe_count: int
    observation_count: int
    annotation_count: int

    has_ground_truth: bool
    ground_truth_source: str | None = None


@dataclass(frozen=True)
class EvaluationSceneIndex:
    scenes: list[EvaluationSceneEntry] = field(default_factory=list)
    scenes_by_id: dict[str, EvaluationSceneEntry] = field(default_factory=dict)

    samples_by_id: dict[str, KeyframeSample] = field(default_factory=dict)
    scene_by_sample_id: dict[str, EvaluationSceneEntry] = field(default_factory=dict)

    scene_count: int = 0
    keyframe_count: int = 0
    observation_count: int = 0
    annotation_count: int = 0
    ground_truth_scene_count: int = 0

    def get_sample(self, sample_id: str) -> KeyframeSample | None:
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
    """Ground truth for evaluation: every Scene of the dataset manifest, read
    at its pinned revision and projected into keyframe samples."""
    scenes: list[EvaluationSceneEntry] = []
    scenes_by_id: dict[str, EvaluationSceneEntry] = {}
    samples_by_id: dict[str, KeyframeSample] = {}
    scene_by_sample_id: dict[str, EvaluationSceneEntry] = {}

    for index_entry in dataset_manifest.scenes:
        manifest = await load_pinned_scene(scene_artifact_store, index_entry)
        samples = keyframe_samples(scene_id=index_entry.scene_id, manifest=manifest)
        annotation_count = len(manifest.annotations)

        entry = EvaluationSceneEntry(
            scene_id=index_entry.scene_id,
            manifest=manifest,
            keyframe_count=len(samples),
            observation_count=len(manifest.observations),
            annotation_count=annotation_count,
            has_ground_truth=annotation_count > 0,
            ground_truth_source=None,
        )
        scenes.append(entry)
        scenes_by_id[entry.scene_id] = entry
        for sample in samples:
            samples_by_id[sample.sample_id] = sample
            scene_by_sample_id[sample.sample_id] = entry

    return EvaluationSceneIndex(
        scenes=scenes,
        scenes_by_id=scenes_by_id,
        samples_by_id=samples_by_id,
        scene_by_sample_id=scene_by_sample_id,
        scene_count=len(scenes),
        keyframe_count=sum(s.keyframe_count for s in scenes),
        observation_count=sum(s.observation_count for s in scenes),
        annotation_count=sum(s.annotation_count for s in scenes),
        ground_truth_scene_count=sum(1 for s in scenes if s.has_ground_truth),
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
