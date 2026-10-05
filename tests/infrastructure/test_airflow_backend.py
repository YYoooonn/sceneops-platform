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
"""

from __future__ import annotations

import os

import pytest

from infra_support import episode_params, scene_params

pytestmark = pytest.mark.skipif(
    os.environ.get("SCENEOPS_TEST_AIRFLOW") != "1",
    reason="Airflow acceptance is opt-in: make test-infrastructure-airflow",
)


def _executed_by_airflow(api, run: dict) -> None:
    executions = api.get("/executions", resource_id=run["pipelineRunId"])["executions"]
    assert {e["executionBackend"] for e in executions} == {"airflow"}


def test_recording_scene_building_through_airflow(api, baseline):
    dataset = api.new_dataset_version("airflow-scene")
    run = api.run("recording_scene_building", dataset, scene_params(baseline["robot_run_ids"][0]))

    assert run["status"] == "succeeded", run
    _executed_by_airflow(api, run)
    assert len(api.scenes(dataset)) == baseline["scene_count"]


def test_recording_episode_building_through_airflow(api, baseline):
    dataset = api.new_dataset_version("airflow-episode")
    run = api.run(
        "recording_episode_building", dataset, episode_params(baseline["robot_run_ids"][0])
    )

    assert run["status"] == "succeeded", run
    _executed_by_airflow(api, run)
    assert len(api.episodes(dataset)) == baseline["episode_count"]


def test_episode_learning_data_building_through_airflow(api, baseline):
    """The L3 pipeline over the baseline's pinned Episodes."""
    dataset = (baseline["dataset_id"], baseline["dataset_version"])
    pins = [
        {
            "episode_id": e["episodeId"],
            "source_artifact_id": e["manifestArtifactId"],
            "source_manifest_sha256": e["manifestChecksum"].removeprefix("sha256:"),
        }
        for e in api.episodes(dataset)
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


def test_scene_ml_evaluation_through_airflow(api, baseline):
    """The L3 Scene ML pipeline over the baseline, with the label set
    `make e2e-scene-ml` imported for it (`BASELINE_ID=canonical`). A label
    document reaches the platform only through that journey, so the test is
    skipped on a baseline that has none."""
    dataset = (baseline["dataset_id"], baseline["dataset_version"])
    run_id = baseline["robot_run_ids"][0]
    unit = "scene-" + run_id.rsplit("-scene-", 1)[-1]
    label_set_id = f"labels-{baseline['baseline_id']}-{unit}"
    revisions = api.get(
        "/artifacts", kind="label_set_manifest", owner_type="label_set", owner_id=label_set_id, limit=1
    )["artifacts"]
    if not revisions:
        pytest.skip(f"label set {label_set_id} is not imported; run `make e2e-scene-ml BASELINE_ID=canonical`")
    label_set = {
        "label_set_id": label_set_id,
        "manifest_artifact_id": revisions[0]["artifactId"],
        "manifest_checksum": revisions[0]["checksum"],
    }
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
                },
                "score_scenario_readiness": {},
                "predict_detection": {"inference_backend": "mock", "camera_channel": camera},
                "evaluate_detection": {"label_set": label_set, "match_distance_m": 2.0},
            },
        },
    ).json()["pipelineRun"]
    api.dispatch(created["pipelineRunId"])
    run = api.wait(created["pipelineRunId"])

    assert run["status"] == "succeeded", run
    _executed_by_airflow(api, run)
