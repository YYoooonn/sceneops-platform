"""The ONNX backend's contract predictions read canonical keyframe
annotations (category, box, annotation_id)."""

from __future__ import annotations

from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_worker.inference.detection.onnx_runtime import (
    _build_contract_predictions_from_sample,
)
from sceneops_worker.scenes.keyframes import keyframe_samples


def test_contract_predictions_come_from_canonical_annotations():
    manifest = build_scene_manifest(
        keyframe_timestamps_ns=(1_000,), annotations_per_keyframe=2
    )
    [sample] = keyframe_samples(scene_id="scene-1", manifest=manifest)

    predictions = _build_contract_predictions_from_sample(sample)

    assert [p["source_annotation_token"] for p in predictions] == [
        "ann-0000-00",
        "ann-0000-01",
    ]
    first = predictions[0]
    assert first["category_name"] == "vehicle.car"
    assert first["translation"] == [10.0, 2.0, 0.5]
    assert first["size"] == [1.9, 4.5, 1.6]
    assert first["rotation"] == [1.0, 0.0, 0.0, 0.0]


def test_unsupported_categories_are_skipped():
    manifest = build_scene_manifest(keyframe_timestamps_ns=(1_000,), category="animal")
    [sample] = keyframe_samples(scene_id="scene-1", manifest=manifest)
    assert _build_contract_predictions_from_sample(sample) == []
