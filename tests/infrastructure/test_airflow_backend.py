"""Pipelines through the Airflow per-task DAGs (airflow/dags/sceneops_pipelines.py).

Opt-in: the Airflow stack is not part of `make local-up`, and the API picks its
pipeline backend at process start, so this module needs

    make airflow-up
    SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow  (api restarted with it)
    SCENEOPS_TEST_AIRFLOW=1 make test-infrastructure-airflow

Each DAG runs every task of one pipeline as its own DockerOperator process,
recomposing the state transitions of the Celery path (start / finalize). The
tests prove the canonical pipelines reach the same terminal state and the same
canonical records through that orchestrator.

The tests own the fixed Dataset `sceneops-test-infra-airflow`, one DatasetVersion per test
(REFERENCE_DERIVED, docs/development/test-matrix.md). A repeated run reuses them: each
pipeline is executed again through Airflow and converges on the records already there.
"""

from __future__ import annotations

import os

import pytest

from infra_support import (
    INFRA_AIRFLOW_DATASET,
    episode_params,
    reference_label_document,
    scene_params,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("SCENEOPS_TEST_AIRFLOW") != "1",
    reason="Airflow acceptance is opt-in: make test-infrastructure-airflow",
)


def _executed_by_airflow(api, run: dict) -> None:
    executions = api.get("/executions", resource_id=run["pipelineRunId"])["executions"]
    assert {e["executionBackend"] for e in executions} == {"airflow"}


def test_recording_scene_building_through_airflow(api, baseline_run):
    dataset = api.dataset_version(INFRA_AIRFLOW_DATASET, "scene")
    run = api.run("recording_scene_building", dataset, scene_params(baseline_run["robot_run_id"]))

    assert run["status"] == "succeeded", run
    _executed_by_airflow(api, run)
    assert len(api.scenes(dataset)) == baseline_run["scene_count"]


def test_recording_episode_building_through_airflow(api, baseline_run):
    dataset = api.dataset_version(INFRA_AIRFLOW_DATASET, "episode")
    run = api.run(
        "recording_episode_building", dataset, episode_params(baseline_run["robot_run_id"])
    )

    assert run["status"] == "succeeded", run
    _executed_by_airflow(api, run)
    assert len(api.episodes(dataset)) == baseline_run["episode_count"]


def test_episode_learning_data_building_through_airflow(api, baseline_run):
    """The L3 pipeline over one RobotRun's pinned Episodes, built into the test's own
    DatasetVersion: the reference DatasetVersion is never written to."""
    dataset = api.dataset_version(INFRA_AIRFLOW_DATASET, "learning")
    built = api.run(
        "recording_episode_building", dataset, episode_params(baseline_run["robot_run_id"])
    )
    assert built["status"] == "succeeded", built
    pins = [
        {
            "episode_id": e["episodeId"],
            "source_artifact_id": e["manifestArtifactId"],
            "source_manifest_sha256": e["manifestChecksum"].removeprefix("sha256:"),
        }
        for e in api.episodes(dataset)
        if e["robotRunId"] == baseline_run["robot_run_id"]
    ]
    run = api.run(
        "episode_learning_data_building",
        dataset,
        {
            "align_episode": {
                "episodes": pins,
                "alignment_config": {
                    "target_frequency_hz": 5.0,
                    "tolerance_us": 200000,
                    "max_gap_us": 1000000,
                },
            }
        },
    )

    assert run["status"] == "succeeded", run
    _executed_by_airflow(api, run)


def test_scene_ml_evaluation_through_airflow(api, baseline, baseline_run):
    """The L3 Scene ML pipeline over the Scenes of the test's own DatasetVersion, with a
    LabelSet the test itself imports (the fixture's locked reference labels, rendered for
    the baseline RobotRun). The ScenarioSet, inference run and evaluation run carry fixed
    ids, so a repeated run converges on the same derived records instead of adding new
    ones. The reference DatasetVersion is never written to; a missing prerequisite
    (reference labels, the acquisition image) fails."""
    dataset = api.dataset_version(INFRA_AIRFLOW_DATASET, "scene-ml")
    built = api.run("recording_scene_building", dataset, scene_params(baseline_run["robot_run_id"]))
    assert built["status"] == "succeeded", built

    label_set_id = f"labels-{dataset[0]}-{baseline_run['source_unit']}"
    with reference_label_document(
        baseline["reference"]["corpus"],
        baseline_run["fixture_id"],
        baseline_run["robot_run_id"],
        label_set_id,
    ) as document_uri:
        imported = api.run_job("import_labels", dataset, {"document_uri": document_uri})
    assert imported["status"] == "succeeded", imported
    label_set = imported["result"]["label_set"]
    assert label_set["label_set_id"] == label_set_id, label_set

    camera, lidar = "/camera/front/image/compressed", "/lidar/top/points"
    views = {
        "policy": {
            "anchor": {"channel": camera, "stride": 1},
            "members": [
                {"channel": lidar, "association": "nearest", "tolerance_ns": 25_000_000, "required": True}
            ],
            "pose": {
                "parent_frame_id": "map",
                "child_frame_id": "base_link",
                "association": "nearest",
                "tolerance_ns": 5_000_000,
                "required": True,
            },
        },
        "label_sets": [label_set],
    }
    api.post("/models", {"modelId": "infra-detector", "name": "Infrastructure detector", "metadata": {}})
    api.post(
        "/models/infra-detector/versions", {"version": "v1", "backend": "mock", "metadata": {}}
    )
    created = api.post(
        "/pipelines/runs",
        {
            "type": "scene_ml_evaluation",
            "dataset_id": dataset[0],
            "dataset_version": dataset[1],
            "model_id": "infra-detector",
            "model_version": "v1",
            "force": True,
            "params": {
                "build_scene_sample_views": views,
                "mine_scenarios": {
                    "label_set_id": label_set_id,
                    "require_labels": True,
                    "required_channels": [camera, lidar],
                    "output_scenario_set_id": "scset-infra-airflow",
                },
                "score_scenario_readiness": {},
                "predict_detection": {
                    "inference_backend": "mock",
                    "camera_channel": camera,
                    "inference_run_id": "infer-infra-airflow",
                },
                "evaluate_detection": {
                    "label_set": label_set,
                    "match_distance_m": 2.0,
                    "evaluation_run_id": "eval-infra-airflow",
                },
            },
        },
    ).json()["pipelineRun"]
    api.dispatch(created["pipelineRunId"])
    run = api.wait(created["pipelineRunId"])

    assert run["status"] == "succeeded", run
    _executed_by_airflow(api, run)
