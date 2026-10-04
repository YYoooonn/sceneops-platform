"""Unit tests for CenterDistanceDetectionEvaluator scene-level behavior.

Covers:
- GT scene with samples that have zero annotations is evaluated (valid negative)
- Prediction shard for non-GT scene is skipped and recorded in skipped_shards
- skipped_scene_ids is populated in evaluation manifest metadata
- missing_gt_policy=fail raises when a non-GT shard is encountered
- shard.scene_id / payload scene_id mismatch produces a warning
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.datasets.schemas import DatasetManifest, DatasetSceneIndexEntry
from sceneops_core.inference.schemas.manifests import (
    DetectionPredictionManifest,
    DetectionPredictionShardRef,
)
from sceneops_core.scenes.schemas import SceneManifest, scene_id_for
from sceneops_core.scenes.testing import recording_source
from sceneops_worker.evaluation.detection.base import DetectionEvaluationRequest
from sceneops_worker.evaluation.detection.center_distance import (
    evaluate_center_distance_detection,
)
from sceneops_worker.scenes.keyframes import keyframe_sample_id


# ── builders ──────────────────────────────────────────────────────────────────


class _Scenes:
    """Canonical Scenes published into the test's local ArtifactStore, by
    readable name. Each Scene has one source keyframe, so one sample."""

    def __init__(self, world) -> None:
        self.world = world
        self.entries: dict[str, DatasetSceneIndexEntry] = {}

    async def add(
        self,
        name: str,
        *,
        has_ground_truth: bool = True,
        unannotated_keyframe: bool = False,
    ) -> None:
        """``unannotated_keyframe`` adds a second keyframe (keyframe-0001)
        that carries no annotations."""
        source = recording_source(unit_key=name)
        manifest = self.world.manifest(
            source=source,
            keyframe_timestamps_ns=(1_000, 2_000) if unannotated_keyframe else (1_000,),
            annotations_per_keyframe=1 if has_ground_truth else 0,
        )
        if unannotated_keyframe:
            payload = json.loads(manifest.to_canonical_bytes())
            payload["annotations"] = [
                a for a in payload["annotations"] if a["group_id"] != "keyframe-0001"
            ]
            manifest = SceneManifest.model_validate(payload)
        artifact = await self.world.publish(manifest)
        self.entries[name] = DatasetSceneIndexEntry(
            scene_id=scene_id_for(dataset_id="ds", dataset_version="v1", source=source),
            manifest_artifact_id=artifact.artifact_id,
            manifest_checksum=artifact.checksum,
            manifest_uri=artifact.uri,
        )

    def scene_id(self, name: str) -> str:
        return self.entries[name].scene_id

    def sample_id(self, name: str, keyframe: int = 0) -> str:
        return keyframe_sample_id(self.scene_id(name), f"keyframe-{keyframe:04d}")


def _prediction_manifest(
    shards: list[DetectionPredictionShardRef],
) -> DetectionPredictionManifest:
    return DetectionPredictionManifest(
        inference_run_id="infer-001",
        dataset_id="ds",
        dataset_version="v1",
        model_id="dummy",
        model_version="v1",
        prediction_shards=shards,
    )


def _shard(
    scene_id: str, sample_id: str, uri: str, prediction_count: int = 3
) -> DetectionPredictionShardRef:
    return DetectionPredictionShardRef(
        scene_id=scene_id,
        sample_id=sample_id,
        uri=uri,
        prediction_count=prediction_count,
    )


def _sample_payload(
    scene_id: str, sample_id: str, predictions: list[dict] | None = None
) -> dict:
    return {
        "dataset_id": "ds",
        "dataset_version": "v1",
        "scene_id": scene_id,
        "sample_id": sample_id,
        "predictions": predictions or [],
    }


def _build_request(
    scenes: _Scenes,
    shards: list[DetectionPredictionShardRef],
    sample_payloads: dict[str, dict],
    missing_gt_policy: str = "skip",
) -> DetectionEvaluationRequest:
    prediction_manifest = _prediction_manifest(shards)
    dataset_manifest = DatasetManifest(
        dataset_id="ds", dataset_version="v1", scenes=list(scenes.entries.values())
    )

    run_store = MagicMock()
    run_store.load_inference_prediction_manifest = AsyncMock(
        return_value=prediction_manifest
    )

    async def load_sample(uri: str) -> dict:
        return sample_payloads[uri]

    run_store.load_sample_prediction_manifest = load_sample
    run_store.write_evaluation_run_manifest = AsyncMock(return_value=None)
    run_store.write_sample_evaluation_manifest = AsyncMock(return_value=None)
    run_store.evaluation_run_manifest_uri = MagicMock(
        return_value="file:///eval/evaluation.json"
    )
    run_store.evaluation_run_metrics_uri = MagicMock(
        return_value="file:///eval/metrics.json"
    )
    run_store.evaluation_samples_root_uri = MagicMock(
        return_value="file:///eval/samples/"
    )

    return DetectionEvaluationRequest(
        dataset_manifest=dataset_manifest,
        scene_artifact_store=scenes.world.scene_artifact_store,
        run_artifact_store=run_store,
        inference_run_id="infer-001",
        evaluation_run_id="eval-001",
        match_distance_m=2.0,
        missing_gt_policy=missing_gt_policy,
    )


@pytest.fixture()
def scenes(scene_world) -> _Scenes:
    return _Scenes(scene_world)


def _shard_and_payload(
    scenes, name, uri, *, keyframe=0, prediction_count=3, predictions=None
):
    scene_id, sample_id = scenes.scene_id(name), scenes.sample_id(name, keyframe)
    return (
        _shard(scene_id, sample_id, uri, prediction_count=prediction_count),
        {uri: _sample_payload(scene_id, sample_id, predictions)},
    )


# ── GT scene with zero-annotation keyframe is evaluated, not skipped ──────────


async def test_zero_annotation_sample_in_gt_scene_is_evaluated(scenes):
    """A keyframe inside a GT-bearing Scene is evaluated even with 0
    annotations: it is a valid negative."""
    await scenes.add("gt", unannotated_keyframe=True)
    shard, payloads = _shard_and_payload(
        scenes, "gt", "file:///preds/k1.json", keyframe=1, prediction_count=0
    )

    manifest = await evaluate_center_distance_detection(
        _build_request(scenes, [shard], payloads)
    )

    assert manifest.status == "succeeded"
    assert manifest.sample_count == 1


# ── non-GT scene shard is skipped ─────────────────────────────────────────────


async def test_non_gt_scene_shard_skipped_and_recorded(scenes):
    await scenes.add("gt")
    await scenes.add("no-gt", has_ground_truth=False)
    shard_gt, payload_gt = _shard_and_payload(scenes, "gt", "file:///preds/gt.json")
    shard_no_gt, payload_no_gt = _shard_and_payload(
        scenes, "no-gt", "file:///preds/no.json"
    )

    manifest = await evaluate_center_distance_detection(
        _build_request(scenes, [shard_gt, shard_no_gt], {**payload_gt, **payload_no_gt})
    )

    skipped = manifest.metadata.get("skipped_shards", [])
    no_gt_skipped = [s for s in skipped if s["scene_id"] == scenes.scene_id("no-gt")]
    assert len(no_gt_skipped) == 1
    assert no_gt_skipped[0]["reason"] == "scene_has_no_ground_truth"
    assert manifest.metadata["skipped_scene_ids"] == [scenes.scene_id("no-gt")]


# ── missing_gt_policy=fail raises on non-GT shard ────────────────────────────


async def test_fail_policy_raises_on_non_gt_shard(scenes):
    await scenes.add("gt")
    await scenes.add("no-gt", has_ground_truth=False)
    shard_gt, payload_gt = _shard_and_payload(scenes, "gt", "file:///preds/gt.json")
    shard_no_gt, payload_no_gt = _shard_and_payload(
        scenes, "no-gt", "file:///preds/no.json"
    )

    with pytest.raises(ValueError, match=scenes.scene_id("no-gt")):
        await evaluate_center_distance_detection(
            _build_request(
                scenes,
                [shard_gt, shard_no_gt],
                {**payload_gt, **payload_no_gt},
                missing_gt_policy="fail",
            )
        )


# ── shard scene_id / payload scene_id mismatch → warning ─────────────────────


async def test_shard_scene_id_mismatch_with_payload_produces_warning(scenes):
    await scenes.add("a")
    await scenes.add("b")
    uri = "file:///preds/b.json"
    shard = _shard(scenes.scene_id("a"), scenes.sample_id("b"), uri, prediction_count=0)
    payload = {uri: _sample_payload(scenes.scene_id("b"), scenes.sample_id("b"))}

    manifest = await evaluate_center_distance_detection(
        _build_request(scenes, [shard], payload)
    )

    mismatch = [
        w
        for w in manifest.metadata.get("warnings", [])
        if w.get("type") == "shard_scene_id_mismatch"
    ]
    assert len(mismatch) == 1
    assert mismatch[0]["shard_scene_id"] == scenes.scene_id("a")
    assert mismatch[0]["payload_scene_id"] == scenes.scene_id("b")


# ── matching uses canonical annotation boxes ──────────────────────────────────


async def test_prediction_at_the_annotated_box_center_matches(scenes):
    await scenes.add("gt")
    prediction = {
        "prediction_id": "p-1",
        "category_name": "vehicle.car",
        "translation": [10.0, 2.0, 0.5],
        "lifting_status": "succeeded",
    }
    shard, payloads = _shard_and_payload(
        scenes,
        "gt",
        "file:///preds/gt.json",
        prediction_count=1,
        predictions=[prediction],
    )

    manifest = await evaluate_center_distance_detection(
        _build_request(scenes, [shard], payloads)
    )

    assert manifest.metadata["evaluated_scene_ids"] == [scenes.scene_id("gt")]
    assert manifest.metadata["skipped_scene_ids"] == []
    index = manifest.metadata["scene_index"]
    assert index["scenes"][0]["scene_id"] == scenes.scene_id("gt")
    assert manifest.metrics["tp"] == 1
