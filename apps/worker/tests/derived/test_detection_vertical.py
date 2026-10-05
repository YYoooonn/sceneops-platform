"""Detection over pinned derived inputs, end to end through the real job
handlers and stores (ADR-007 §33.5):

    registered Scenes -> imported label set -> sample views
        -> ScenarioSet -> predict_detection -> evaluate_detection

Every step pins the exact revision it consumed, and a consumer that is handed
anything that does not resolve to exactly that revision fails loudly.
"""

from __future__ import annotations


import pytest

from sceneops_core.jobs.schemas import JobType
from sceneops_worker.derived import DerivedManifestIntegrityError
from sceneops_worker.derived.resolution import LegacyDerivedRecordError
from sceneops_worker.evaluation.detection.utils import FrameMismatchError
from sceneops_worker.inference.detection.samples import SampleViewResolutionError
from sceneops_worker.jobs.inference.predict_detection import DatasetScopeMismatchError
from tests.derived.flow_support import (
    T0,
    T1,
    build_views,
    evaluate,
    import_label_set,
    mine,
    predict,
)
from tests.derived.labels_support import label_document, write_document
from tests.derived_harness import make_harness


async def _world(tmp_path):
    """Scene A: three labels on a keyframe; Scene B: covered, no objects;
    Scene C: not covered by the label set at all."""
    harness = make_harness(tmp_path)
    scenes = {
        name: await harness.register_scene(robot_run_id=run)
        for name, run in (("a", "run-001"), ("b", "run-002"), ("c", "run-003"))
    }
    from sceneops_core.jobs.schemas import ImportLabelsJobParams
    from sceneops_core.labels import LabelSetManifest
    from sceneops_worker.jobs.derived import ImportLabelsJobHandler

    a = label_document(
        "gt",
        run="run-001",
        covered=[T0, T1],
        labels={f"a{i}": (T0, 10.0 + 6 * i) for i in range(3)},
    )
    b = label_document("gt", run="run-002", covered=[T0, T1])
    merged = LabelSetManifest.normalized(
        label_set_id="gt",
        provenance=a.provenance,
        coverage=[*a.coverage, *b.coverage],
        labels=list(a.labels),
    )
    imported = await ImportLabelsJobHandler().run(
        harness.request(
            JobType.IMPORT_LABELS,
            ImportLabelsJobParams(
                document_uri=await write_document(harness, "gt.json", merged)
            ),
        )
    )
    built = await build_views(harness, label_sets=[imported.label_set])
    return harness, scenes, imported.label_set, built.views


async def test_scenario_set_to_prediction_to_evaluation_pins_every_revision(tmp_path):
    harness, scenes, label_set, views = await _world(tmp_path)
    mined = await mine(harness, views, label_set_id="gt", require_labels=False)

    prediction = await predict(harness, scenario_set_id=mined.scenario_set_id)
    # Scenes A, B, C each contribute their two keyframe samples (the sweep was dropped).
    assert (prediction.scene_count, prediction.sample_count) == (3, 6)
    assert prediction.prediction_manifest_checksum

    run = harness.inference_runs.records[prediction.inference_run_id]
    assert run.prediction_manifest_checksum == prediction.prediction_manifest_checksum
    pins = run.metadata["inputs"]
    assert {v["scene_id"] for v in pins["sample_views"]} == {
        s.scene_id for s in scenes.values()
    }
    assert pins["scenario_set"]["manifest_checksum"] == mined.scenario_set_checksum

    manifest = await harness.derived_store.read_prediction_manifest(
        uri=prediction.prediction_manifest_uri,
        checksum=prediction.prediction_manifest_checksum,
    )
    assert manifest.scenario_set.manifest_checksum == mined.scenario_set_checksum
    assert manifest.config["camera_channel"] == "CAM_FRONT"
    assert manifest.config["lidar_channel"] is None
    assert {i.sample_view for i in manifest.inputs} == set(views)

    evaluation = await evaluate(
        harness, inference_run_id=prediction.inference_run_id, label_set=label_set
    )
    assert (
        evaluation.prediction_manifest_checksum
        == prediction.prediction_manifest_checksum
    )
    assert (evaluation.label_set_id, evaluation.label_set_checksum) == (
        "gt",
        label_set.manifest_checksum,
    )
    # Scene C is not covered by the label set: skipped, not scored as negatives.
    assert evaluation.sample_count == 4
    assert evaluation.ground_truth_count is not None
    run = harness.evaluation_runs.records[evaluation.evaluation_run_id]
    inputs = run.summary["inputs"]
    assert (
        inputs["prediction"]["manifest_checksum"]
        == prediction.prediction_manifest_checksum
    )
    assert inputs["label_set"]["manifest_checksum"] == label_set.manifest_checksum
    assert inputs["scenario_set"]["manifest_checksum"] == mined.scenario_set_checksum
    evaluation_manifest = await harness.artifact_store.read_json(
        evaluation.evaluation_manifest_uri
    )
    assert (
        evaluation_manifest["inputs"]["label_set"]["manifest_checksum"]
        == label_set.manifest_checksum
    )


async def test_evaluation_counts_only_covered_samples_and_their_labels(tmp_path):
    harness, scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views, max_samples=None)
    evaluation = await evaluate(
        harness, inference_run_id=prediction.inference_run_id, label_set=label_set
    )
    metrics = evaluation.metrics
    # Ground truth = the 3 labels of scene A; scene B contributes covered negatives only.
    assert metrics["tp"] + metrics["fn"] == 3
    assert evaluation.metadata["missing_gt_policy"] == "skip"
    manifest = await harness.artifact_store.read_json(
        evaluation.evaluation_manifest_uri
    )
    assert manifest["metadata"]["skipped_shard_count"] == 2  # scene C's two samples
    assert (
        manifest["metadata"]["skipped_shards"][0]["reason"]
        == "sample_not_covered_by_label_set"
    )


async def test_the_mock_backend_is_deterministic_per_run(tmp_path):
    harness, _scenes, _ls, views = await _world(tmp_path)
    one = await predict(harness, sample_views=views, inference_run_id="run-fixed")
    manifest = await harness.derived_store.read_prediction_manifest(
        uri=one.prediction_manifest_uri, checksum=one.prediction_manifest_checksum
    )
    # Re-running the same run id reproduces the same prediction revision.
    harness.inference_runs.records.clear()
    two = await predict(harness, sample_views=views, inference_run_id="run-fixed")
    assert two.prediction_manifest_checksum == one.prediction_manifest_checksum
    assert manifest.prediction_count == one.prediction_count


async def test_the_uncovered_policy_can_fail_the_evaluation(tmp_path):
    harness, _scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views)
    with pytest.raises(ValueError, match="not covered by label set"):
        await evaluate(
            harness,
            inference_run_id=prediction.inference_run_id,
            label_set=label_set,
            missing_gt_policy="fail",
        )


async def test_categories_restrict_both_labels_and_predictions(tmp_path):
    harness, _scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views)
    evaluation = await evaluate(
        harness,
        inference_run_id=prediction.inference_run_id,
        label_set=label_set,
        categories=["human.pedestrian"],
    )
    assert evaluation.metrics["tp"] == evaluation.metrics["fn"] == 0
    assert evaluation.class_metrics == {}


def test_a_prediction_in_another_frame_fails_loudly():
    from sceneops_worker.evaluation.detection import utils

    labels = list(label_document("x", covered=[T0], labels={"l": (T0, 1.0)}).labels)
    with pytest.raises(FrameMismatchError, match="base_link"):
        utils.evaluate_sample(
            scene_id="s",
            sample_id="smp",
            labels=labels,
            predictions=[
                {
                    "prediction_id": "p",
                    "category_name": "vehicle.car",
                    "frame_id": "base_link",
                    "translation": [1.0, 2.0, 0.5],
                }
            ],
            match_distance_m=2.0,
        )


async def test_tampered_prediction_shards_are_detected(tmp_path):
    harness, _scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views)
    manifest = await harness.derived_store.read_prediction_manifest(
        uri=prediction.prediction_manifest_uri,
        checksum=prediction.prediction_manifest_checksum,
    )
    shard = next(s for s in manifest.prediction_shards if s.prediction_count)
    await harness.artifact_store.write_bytes(shard.uri, b'{"tampered": true}')
    with pytest.raises(Exception, match="pinned checksum"):
        await evaluate(
            harness, inference_run_id=prediction.inference_run_id, label_set=label_set
        )


async def test_a_tampered_prediction_manifest_is_detected(tmp_path):
    harness, _scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views)
    await harness.artifact_store.write_bytes(prediction.prediction_manifest_uri, b"{}")
    with pytest.raises(DerivedManifestIntegrityError, match="checksum"):
        await evaluate(
            harness, inference_run_id=prediction.inference_run_id, label_set=label_set
        )


async def test_the_prediction_revision_can_be_pinned_and_a_wrong_pin_fails(tmp_path):
    harness, _scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views)
    ok = await evaluate(
        harness,
        inference_run_id=prediction.inference_run_id,
        label_set=label_set,
        prediction_manifest_checksum=prediction.prediction_manifest_checksum,
    )
    assert ok.prediction_manifest_checksum == prediction.prediction_manifest_checksum
    with pytest.raises(DerivedManifestIntegrityError, match="not the pinned"):
        await evaluate(
            harness,
            inference_run_id=prediction.inference_run_id,
            label_set=label_set,
            prediction_manifest_checksum="sha256:" + "0" * 64,
        )


async def test_evaluating_against_a_label_set_the_views_do_not_pin_fails(tmp_path):
    harness, _scenes, label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views)
    other = await import_label_set(harness, "other-gt", run="run-001", covered=[T0])
    with pytest.raises(DerivedManifestIntegrityError, match="does not pin label set"):
        await evaluate(
            harness, inference_run_id=prediction.inference_run_id, label_set=other
        )
    # A *different revision* of the pinned set is not the pinned set either.
    revised = await import_label_set(
        harness, "gt", run="run-001", covered=[T0, T1], labels={"x": (T0, 99.0)}
    )
    assert revised.manifest_checksum != label_set.manifest_checksum
    with pytest.raises(DerivedManifestIntegrityError, match="does not pin label set"):
        await evaluate(
            harness, inference_run_id=prediction.inference_run_id, label_set=revised
        )


async def test_legacy_inference_runs_are_refused(tmp_path):
    from sceneops_core.inference.schemas.runs import InferenceRunRecord
    from sceneops_core.runs.schemas import RunStatus

    harness, _scenes, label_set, _views = await _world(tmp_path)
    harness.inference_runs.records["old"] = InferenceRunRecord(
        run_id="old",
        dataset_id="ds",
        dataset_version="v1",
        model_id="m",
        model_version="1",
        status=RunStatus.SUCCEEDED,
        prediction_manifest_uri="file:///old.json",
    )
    with pytest.raises(LegacyDerivedRecordError, match="run it again"):
        await evaluate(harness, inference_run_id="old", label_set=label_set)


async def test_predict_refuses_blocked_scene_revisions(tmp_path):
    harness, scenes, _label_set, views = await _world(tmp_path)
    harness.add_validation(scenes["a"], status="failed", block=True)
    with pytest.raises(ValueError, match="blocked downstream use"):
        await predict(harness, sample_views=views)


async def test_predict_refuses_views_of_another_dataset_version(tmp_path):
    harness, _scenes, _label_set, views = await _world(tmp_path)
    with pytest.raises(DatasetScopeMismatchError):
        await predict(harness, sample_views=views, dataset_version="v2")


async def test_predict_requires_the_camera_channel_to_be_associated(tmp_path):
    harness, _scenes, _label_set, views = await _world(tmp_path)
    with pytest.raises(SampleViewResolutionError, match="associates no 'CAM_NOPE'"):
        await predict(harness, sample_views=views, camera_channel="CAM_NOPE")


async def test_max_samples_caps_the_run_in_scene_sample_order(tmp_path):
    harness, scenes, _label_set, views = await _world(tmp_path)
    prediction = await predict(harness, sample_views=views, max_samples=3)
    assert prediction.sample_count == 3
    manifest = await harness.derived_store.read_prediction_manifest(
        uri=prediction.prediction_manifest_uri,
        checksum=prediction.prediction_manifest_checksum,
    )
    first_two = sorted(s.scene_id for s in scenes.values())[:2]
    assert [i.sample_view.scene_id for i in manifest.inputs] == first_two
    assert manifest.config["max_samples"] == 3


async def test_a_scenario_set_selects_exactly_its_samples(tmp_path):
    harness, scenes, _label_set, views = await _world(tmp_path)
    mined = await mine(harness, views, label_set_id="gt", require_labels=True)
    prediction = await predict(harness, scenario_set_id=mined.scenario_set_id)
    # Only scene A is labelled; only its covered samples run.
    assert (prediction.scene_count, prediction.sample_count) == (1, 2)


async def test_predict_needs_exactly_one_input_source():
    from pydantic import ValidationError
    from sceneops_core.jobs.schemas import PredictDetectionJobParams

    with pytest.raises(ValidationError, match="exactly one"):
        PredictDetectionJobParams(
            dataset_id="d",
            dataset_version="v",
            model_id="m",
            model_version="1",
            camera_channel="CAM_FRONT",
        )
