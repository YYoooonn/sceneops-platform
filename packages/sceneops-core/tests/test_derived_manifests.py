"""ScenarioSet and prediction manifests: canonical form and pin invariants
(ADR-007 §33.4, §33.5)."""

from __future__ import annotations

import json

import pytest

from sceneops_core.common.derived_ids import (
    prediction_manifest_artifact_id,
    scenario_set_artifact_id,
)
from sceneops_core.inference.schemas import (
    DetectionPredictionManifest,
    DetectionPredictionShardRef,
    PredictionInputRef,
    load_canonical_prediction_manifest,
)
from sceneops_core.inference.schemas.manifests import (
    NonCanonicalPredictionManifestError,
)
from sceneops_core.jobs.schemas import (
    EvaluateDetectionJobParams,
    MineScenariosJobParams,
    PredictDetectionJobParams,
)
from sceneops_core.labels import LabelSetRef
from sceneops_core.sample_views import SampleViewRef
from sceneops_core.scenarios import (
    NonCanonicalScenarioSetError,
    ScenarioCuration,
    ScenarioMember,
    ScenarioSetManifest,
    load_canonical_scenario_set,
)

CHECKSUM = "sha256:" + "a" * 64
LABEL_SET = LabelSetRef(
    label_set_id="gt-1", manifest_artifact_id="labelset-1", manifest_checksum=CHECKSUM
)


def view(scene_id: str) -> SampleViewRef:
    return SampleViewRef(
        scene_id=scene_id,
        manifest_artifact_id=f"sampleview-{scene_id}",
        manifest_checksum=CHECKSUM,
    )


def member(scene_id: str, *, samples: int = 2, labels: int = 3) -> ScenarioMember:
    return ScenarioMember(
        scene_id=scene_id,
        sample_view=view(scene_id),
        sample_ids=[f"smp-{i:06d}" for i in range(samples)],
        sample_count=samples,
        label_count=labels,
        channels=["CAM_FRONT", "LIDAR_TOP"],
        readiness="ready",
    )


def scenario_set(*scene_ids: str, rejected: int = 0) -> ScenarioSetManifest:
    return ScenarioSetManifest(
        scenario_set_id="scset-1",
        dataset_id="d",
        dataset_version="v",
        curation=ScenarioCuration(
            label_set=LABEL_SET, require_labels=True, max_candidates=10
        ),
        input_scene_count=len(scene_ids) + rejected,
        rejected_scene_count=rejected,
        members=[member(s) for s in scene_ids],
    )


def test_scenario_set_round_trips_in_canonical_form() -> None:
    manifest = scenario_set("scene-a", "scene-b", rejected=1)
    data = manifest.to_canonical_bytes()
    assert load_canonical_scenario_set(data) == manifest
    assert (
        manifest.checksum() == scenario_set("scene-a", "scene-b", rejected=1).checksum()
    )
    with pytest.raises(NonCanonicalScenarioSetError):
        load_canonical_scenario_set(
            json.dumps(manifest.model_dump(mode="json"), indent=2).encode()
        )


def test_scenario_set_membership_order_is_part_of_the_revision() -> None:
    assert (
        scenario_set("scene-a", "scene-b").checksum()
        != scenario_set("scene-b", "scene-a").checksum()
    )


def test_a_scene_is_a_member_only_once() -> None:
    with pytest.raises(ValueError, match="only once"):
        scenario_set("scene-a", "scene-a")


def test_members_and_rejections_must_account_for_every_input() -> None:
    with pytest.raises(ValueError, match="account for every input"):
        ScenarioSetManifest(
            scenario_set_id="scset-1",
            dataset_id="d",
            dataset_version="v",
            curation=ScenarioCuration(max_candidates=10),
            input_scene_count=5,
            rejected_scene_count=0,
            members=[member("scene-a")],
        )


def test_label_criteria_require_a_pinned_label_set() -> None:
    with pytest.raises(ValueError, match="label_set"):
        ScenarioCuration(require_labels=True, max_candidates=1)
    with pytest.raises(ValueError, match="label_set_id"):
        MineScenariosJobParams(
            dataset_id="d",
            dataset_version="v",
            sample_views=[view("s")],
            min_label_count=1,
        )


def test_a_member_view_must_belong_to_its_scene() -> None:
    with pytest.raises(ValueError, match="belong to its scene"):
        ScenarioMember(
            scene_id="scene-a",
            sample_view=view("scene-b"),
            sample_ids=["smp-000000"],
            sample_count=1,
            label_count=0,
            channels=[],
            readiness="ready",
        )


def test_scenario_set_artifact_id_is_revision_scoped() -> None:
    a = scenario_set_artifact_id(scenario_set_id="scset-1", checksum=CHECKSUM)
    assert a == scenario_set_artifact_id(scenario_set_id="scset-1", checksum=CHECKSUM)
    assert a != scenario_set_artifact_id(
        scenario_set_id="scset-1", checksum="sha256:" + "b" * 64
    )


def prediction_manifest(
    *, shards: list[tuple[str, str, int]]
) -> DetectionPredictionManifest:
    inputs = {}
    for scene_id, sample_id, _ in shards:
        inputs.setdefault(scene_id, []).append(sample_id)
    return DetectionPredictionManifest(
        inference_run_id="run-1",
        dataset_id="d",
        dataset_version="v",
        config={"camera_channel": "CAM_FRONT"},
        inputs=[
            PredictionInputRef(sample_view=view(scene), sample_ids=sorted(ids))
            for scene, ids in sorted(inputs.items())
        ],
        scene_count=len(inputs),
        sample_count=len(shards),
        prediction_count=sum(n for *_, n in shards),
        evaluable_prediction_count=sum(n for *_, n in shards),
        lifting_succeeded_count=0,
        lifting_failed_count=0,
        lifting_not_applicable_count=sum(n for *_, n in shards),
        prediction_shards=[
            DetectionPredictionShardRef(
                scene_id=scene,
                sample_id=sample,
                uri=f"file:///p/{scene}/{sample}.json",
                checksum=CHECKSUM,
                prediction_count=n,
            )
            for scene, sample, n in sorted(shards)
        ],
    )


def test_prediction_manifest_round_trips_and_rejects_noncanonical_bytes() -> None:
    manifest = prediction_manifest(
        shards=[("scene-a", "smp-000000", 2), ("scene-b", "smp-000001", 0)]
    )
    assert load_canonical_prediction_manifest(manifest.to_canonical_bytes()) == manifest
    with pytest.raises(NonCanonicalPredictionManifestError):
        load_canonical_prediction_manifest(
            json.dumps(manifest.model_dump(mode="json"), indent=2).encode()
        )
    a = prediction_manifest_artifact_id(
        inference_run_id="run-1", checksum=manifest.checksum()
    )
    assert a != prediction_manifest_artifact_id(
        inference_run_id="run-2", checksum=manifest.checksum()
    )


def test_a_prediction_shard_outside_the_pinned_inputs_is_rejected() -> None:
    manifest = prediction_manifest(shards=[("scene-a", "smp-000000", 1)])
    data = manifest.model_dump(mode="json")
    data["prediction_shards"].append(
        {
            "scene_id": "scene-z",
            "sample_id": "smp-000009",
            "uri": "file:///x",
            "checksum": CHECKSUM,
            "prediction_count": 0,
        }
    )
    with pytest.raises(ValueError, match="outside the pinned inputs"):
        DetectionPredictionManifest.model_validate(data)


def test_prediction_count_must_equal_the_shard_total() -> None:
    manifest = prediction_manifest(shards=[("scene-a", "smp-000000", 2)])
    data = manifest.model_dump(mode="json")
    data["prediction_count"] = 5
    with pytest.raises(ValueError, match="shard total"):
        DetectionPredictionManifest.model_validate(data)


def test_predict_params_need_exactly_one_input_source_and_an_explicit_camera() -> None:
    base = {
        "dataset_id": "d",
        "dataset_version": "v",
        "model_id": "m",
        "model_version": "1",
        "camera_channel": "CAM_FRONT",
    }
    with pytest.raises(ValueError, match="exactly one"):
        PredictDetectionJobParams(**base)
    with pytest.raises(ValueError, match="exactly one"):
        PredictDetectionJobParams(**base, scenario_set_id="s", sample_views=[view("a")])
    assert PredictDetectionJobParams(**base, scenario_set_id="s").lidar_channel is None
    with pytest.raises(ValueError):
        PredictDetectionJobParams(
            **{k: v for k, v in base.items() if k != "camera_channel"},
            scenario_set_id="s",
        )


def test_evaluate_params_always_pin_a_label_set() -> None:
    with pytest.raises(ValueError):
        EvaluateDetectionJobParams(
            dataset_id="d", dataset_version="v", inference_run_id="r"
        )
    params = EvaluateDetectionJobParams(
        dataset_id="d", dataset_version="v", inference_run_id="r", label_set=LABEL_SET
    )
    assert params.categories is None
