"""Pipeline execution contracts: dedup, force, convergence, replacement,
blocked resumption, failure recovery, concurrent registration and the
orchestrator that ran them.

Each test builds the baseline RobotRun's Scenes or Episodes into its own
throwaway DatasetVersion. Retry semantics are properties of the execution
model and the registrars, so they are proven here once, on the two canonical
build pipelines, rather than inside every user journey.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from infra_support import episode_params, scene_params, EPISODE_BUILD_CONFIG, SCENE_BUILD_CONFIG

EXPECTED_BACKEND = os.environ.get("EXPECTED_PIPELINE_BACKEND", "celery")


def _without_timestamps(records: list[dict]) -> list[dict]:
    return sorted(
        ({k: v for k, v in r.items() if k not in ("updatedAt", "registeredAt")} for r in records),
        key=lambda r: r.get("sceneId") or r.get("episodeId"),
    )


def _run_id(baseline: dict) -> str:
    return baseline["robot_run_ids"][0]


# (pipeline type, params builder, record lister, register task, changed config)
SCOPES = {
    "scene": (
        "recording_scene_building",
        scene_params,
        "scenes",
        "register_scenes",
        {**SCENE_BUILD_CONFIG, "segmentation": {**SCENE_BUILD_CONFIG["segmentation"], "duration_ns": 60_000_000_000}},
    ),
    "episode": (
        "recording_episode_building",
        episode_params,
        "episodes",
        "register_episodes",
        {
            **EPISODE_BUILD_CONFIG,
            "segmentation": {
                "policy": "fixed_duration",
                "clock": "vehicle.source_time",
                "duration_ns": 10_000_000_000,
            },
        },
    ),
}


def test_identical_requests_dedup_and_force_creates_a_fresh_run(api, baseline):
    dataset = api.new_dataset_version("dedup")
    params = scene_params(_run_id(baseline))

    first = api.create_run("recording_scene_building", dataset, params)
    again = api.create_run("recording_scene_building", dataset, params)
    forced = api.create_run("recording_scene_building", dataset, params, force=True)
    other_params = api.create_run(
        "recording_scene_building", dataset, scene_params(_run_id(baseline), replace=True)
    )

    assert again["pipelineRunId"] == first["pipelineRunId"]
    assert forced["pipelineRunId"] != first["pipelineRunId"]
    assert other_params["pipelineRunId"] != first["pipelineRunId"]


def test_registering_a_published_recording_again_converges(api, baseline):
    """REGISTER_ROBOT_RUN is idempotent: a re-POST returns the original Job, and a
    forced re-execution reports created=false and changes neither the RobotRun nor
    its ArtifactRecords."""
    run_id = _run_id(baseline)
    run_before = api.get(f"/robot-runs/{run_id}")["robotRun"]
    artifacts_before = api.get(
        "/artifacts", owner_type="robot_run", owner_id=run_id, limit=500
    )["artifacts"]
    manifest_uri = api.get(f"/artifacts/{run_before['manifestArtifactId']}")["artifact"]["uri"]

    first = api.post("/robot-runs:register", {"manifest_uri": manifest_uri}).json()["job"]["jobId"]
    again = api.post("/robot-runs:register", {"manifest_uri": manifest_uri}).json()["job"]["jobId"]
    assert again == first, "an identical registration request returns the original Job"

    forced = api.post(
        "/jobs",
        {
            "type": "register_robot_run",
            "force": True,
            "params": {"manifest_uri": manifest_uri},
        },
        expect=201,
    ).json()["job"]["jobId"]
    api.post(f"/jobs/{forced}/execute", {})
    deadline = time.monotonic() + 120
    job = api.get(f"/jobs/{forced}")["job"]
    while job["status"] not in ("succeeded", "failed") and time.monotonic() < deadline:
        time.sleep(2)
        job = api.get(f"/jobs/{forced}")["job"]

    assert job["status"] == "succeeded", job
    assert job["result"]["created"] is False
    assert api.get(f"/robot-runs/{run_id}")["robotRun"] == run_before
    assert (
        api.get("/artifacts", owner_type="robot_run", owner_id=run_id, limit=500)["artifacts"]
        == artifacts_before
    )


def test_identical_jobs_dedup_and_force_creates_a_fresh_job(api, baseline):
    dataset = (baseline["dataset_id"], baseline["dataset_version"])
    body = {
        "type": "export_analytics_snapshot",
        "dataset_id": dataset[0],
        "dataset_version": dataset[1],
        "params": {"dataset_id": dataset[0], "dataset_version": dataset[1]},
    }
    first = api.post("/jobs", body, expect=201).json()["job"]["jobId"]
    again = api.post("/jobs", body, expect=201).json()["job"]["jobId"]
    forced = api.post("/jobs", {**body, "force": True}, expect=201).json()["job"]["jobId"]

    assert again == first
    assert forced != first


@pytest.mark.parametrize("scope", ["scene", "episode"])
def test_rebuilding_an_unchanged_scope_converges(api, baseline, scope):
    pipeline_type, params_for, listing, register_task, _ = SCOPES[scope]
    dataset = api.new_dataset_version(f"converge-{scope}")
    params = params_for(_run_id(baseline))

    first = api.run(pipeline_type, dataset, params)
    assert first["status"] == "succeeded"
    before = getattr(api, listing)(dataset)
    assert before

    second = api.run(pipeline_type, dataset, params)
    assert second["status"] == "succeeded"
    registered = api.tasks(second["pipelineRunId"])[register_task]["result"]["summary"]

    assert len(registered[f"unchanged_{scope}_ids"]) == len(before)
    assert _without_timestamps(getattr(api, listing)(dataset)) == _without_timestamps(before)


@pytest.mark.parametrize("scope", ["scene", "episode"])
def test_a_changed_configuration_conflicts_until_replacement_is_explicit(api, baseline, scope):
    pipeline_type, params_for, listing, register_task, changed = SCOPES[scope]
    dataset = api.new_dataset_version(f"replace-{scope}")
    original = api.run(pipeline_type, dataset, params_for(_run_id(baseline)))
    assert original["status"] == "succeeded"
    members = getattr(api, listing)(dataset)
    fingerprint = {m["producerFingerprint"] for m in members}

    conflict = api.run(pipeline_type, dataset, params_for(_run_id(baseline), config=changed))
    assert conflict["status"] == "failed"
    assert api.tasks(conflict["pipelineRunId"])[register_task]["status"] == "failed"
    assert _without_timestamps(getattr(api, listing)(dataset)) == _without_timestamps(members), (
        "a refused replacement must leave canonical membership untouched"
    )

    replaced = api.run(
        pipeline_type, dataset, params_for(_run_id(baseline), config=changed, replace=True)
    )
    assert replaced["status"] == "succeeded"
    after = getattr(api, listing)(dataset)
    assert {m["producerFingerprint"] for m in after}.isdisjoint(fingerprint)
    assert len({m["producerFingerprint"] for m in after}) == 1


def test_a_blocked_pipeline_resumes_at_the_blocked_task(api, baseline):
    dataset = api.new_dataset_version("blocked")
    params = scene_params(
        _run_id(baseline),
        validate_scene={"require_target_channels": ["NONEXISTENT_CHANNEL"]},
    )
    created = api.create_run("recording_scene_building", dataset, params)
    run_id = created["pipelineRunId"]

    api.dispatch(run_id)
    assert api.wait(run_id)["status"] == "blocked"

    def finished_tasks() -> dict:
        tasks = api.tasks(run_id)
        return {
            t: (tasks[t]["pipelineTaskRunId"], tasks[t]["jobId"], tasks[t]["status"])
            for t in ("build_recording_scenes", "register_scenes")
        }

    before = finished_tasks()
    assert {status for _, _, status in before.values()} == {"succeeded"}

    assert api.dispatch(run_id).status_code in (200, 202), "a BLOCKED run must be redispatchable"
    assert api.wait(run_id)["status"] == "blocked", "the same params block again"
    assert finished_tasks() == before, "succeeded tasks must not be re-executed on retry"


def test_a_failed_pipeline_is_redispatchable_and_registers_nothing(api):
    dataset = api.new_dataset_version("failed")
    created = api.create_run(
        "recording_scene_building", dataset, scene_params("run-that-was-never-registered")
    )
    run_id = created["pipelineRunId"]

    api.dispatch(run_id)
    first = api.wait(run_id)
    assert first["status"] == "failed"
    build = api.tasks(run_id)["build_recording_scenes"]
    assert build["status"] == "failed" and build["error"]
    assert api.scenes(dataset) == []

    assert api.dispatch(run_id).status_code in (200, 202), "a FAILED run must be redispatchable"
    assert api.wait(run_id)["status"] == "failed"
    assert api.scenes(dataset) == []


def test_concurrent_runs_over_one_scope_converge_on_one_canonical_membership(api, baseline):
    dataset = api.new_dataset_version("concurrent")
    params = scene_params(_run_id(baseline))

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(
            pool.map(lambda _: api.run("recording_scene_building", dataset, params), range(3))
        )

    assert [r["status"] for r in runs] == ["succeeded"] * 3
    scenes = api.scenes(dataset)
    assert len(scenes) == baseline["scene_count"]
    assert len({s["producerFingerprint"] for s in scenes}) == 1
    summary = api.get(f"/datasets/{dataset[0]}/versions/{dataset[1]}")["version"]
    assert summary["scene"]["sceneCount"] == len(scenes), (
        "the registrar-owned DatasetVersion summary equals the registered membership"
    )


def test_pipeline_runs_execute_on_the_configured_orchestrator(api, baseline):
    dataset = api.new_dataset_version("orchestrator")
    run = api.run("recording_scene_building", dataset, scene_params(_run_id(baseline)))
    assert run["status"] == "succeeded"

    executions = api.get("/executions", resource_id=run["pipelineRunId"])["executions"]
    assert executions, "dispatch must leave an execution record"
    assert {e["executionBackend"] for e in executions} == {EXPECTED_BACKEND}
