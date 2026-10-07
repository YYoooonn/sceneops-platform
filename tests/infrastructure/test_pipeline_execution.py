"""Pipeline execution contracts: dedup, force, convergence, replacement,
blocked resumption, failure recovery, concurrent registration, and that every task
of a pipeline runs as a Job through the job execution backend.

Each test builds one baseline RobotRun's Scenes or Episodes into a DatasetVersion it
owns under the fixed Dataset `sceneops-test-infra-pipelines`, named after the test. The
suite runs on a disposable execution runtime (DISPOSABLE_ENVIRONMENT,
docs/development/test-matrix.md), so every test starts from empty state and moves it with
the platform's own replacement semantics; the Jobs and PipelineRuns the re-executions
append are dropped with the runtime. Retry semantics are properties of the execution
model and the registrars, so they are proven here once, on the two canonical build
pipelines, rather than inside every user journey.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from infra_support import (
    EPISODE_BUILD_CONFIG,
    INFRA_PIPELINES_DATASET,
    SCENE_BUILD_CONFIG,
    episode_params,
    scene_params,
)

def _dataset(api, version: str) -> tuple[str, str]:
    return api.dataset_version(INFRA_PIPELINES_DATASET, version)


def _without_timestamps(records: list[dict]) -> list[dict]:
    return sorted(
        ({k: v for k, v in r.items() if k not in ("updatedAt", "registeredAt")} for r in records),
        key=lambda r: r.get("sceneId") or r.get("episodeId"),
    )


# (pipeline type, params builder, record lister, register task, changed config)
SCOPES = {
    "scene": (
        "recording_scene_building",
        scene_params,
        "scenes",
        "register_scenes",
        {
            **SCENE_BUILD_CONFIG,
            "segmentation": {
                "policy": "fixed_duration",
                "clock": SCENE_BUILD_CONFIG["segmentation"]["clock"],
                "duration_ns": 8_000_000_000,
            },
        },
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


def test_identical_requests_dedup_and_force_creates_a_fresh_run(api, baseline_run):
    dataset = _dataset(api, "dedup")
    params = scene_params(baseline_run["robot_run_id"])

    first = api.create_run("recording_scene_building", dataset, params)
    again = api.create_run("recording_scene_building", dataset, params)
    forced = api.create_run("recording_scene_building", dataset, params, force=True)
    other_params = api.create_run(
        "recording_scene_building", dataset, scene_params(baseline_run["robot_run_id"], replace=True)
    )

    assert again["pipelineRunId"] == first["pipelineRunId"]
    assert forced["pipelineRunId"] != first["pipelineRunId"]
    assert other_params["pipelineRunId"] != first["pipelineRunId"]


def test_registering_a_published_recording_again_converges(api, baseline_run):
    """REGISTER_ROBOT_RUN is idempotent: a re-POST returns the original Job, and a
    forced re-execution reports created=false and changes neither the RobotRun nor
    its ArtifactRecords."""
    run_id = baseline_run["robot_run_id"]
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


def test_identical_jobs_dedup_and_force_creates_a_fresh_job(api):
    dataset = _dataset(api, "jobs-dedup")
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
def test_rebuilding_an_unchanged_scope_converges(api, baseline_run, scope):
    pipeline_type, params_for, listing, register_task, _ = SCOPES[scope]
    dataset = _dataset(api, f"converge-{scope}")
    params = params_for(baseline_run["robot_run_id"])

    # Establishes the scope on the first run; a later run finds it built, and the
    # identical request returns the run that built it.
    first = api.run(pipeline_type, dataset, params, force=False)
    assert first["status"] == "succeeded"
    before = getattr(api, listing)(dataset)
    assert before

    second = api.run(pipeline_type, dataset, params)
    assert second["status"] == "succeeded"
    registered = api.tasks(second["pipelineRunId"])[register_task]["result"]["summary"]

    assert len(registered[f"unchanged_{scope}_ids"]) == len(before)
    assert _without_timestamps(getattr(api, listing)(dataset)) == _without_timestamps(before)


@pytest.mark.parametrize("scope", ["scene", "episode"])
def test_a_changed_configuration_conflicts_until_replacement_is_explicit(api, baseline_run, scope):
    pipeline_type, params_for, listing, register_task, changed = SCOPES[scope]
    dataset = _dataset(api, f"replace-{scope}")
    run_id = baseline_run["robot_run_id"]

    # The first build of the scope is an explicit-replacement run too (a forced run,
    # because an identical request would return an earlier run): replace=True is
    # valid on an empty scope and puts the original configuration in place.
    original = api.run(pipeline_type, dataset, params_for(run_id, replace=True))
    assert original["status"] == "succeeded"
    members = getattr(api, listing)(dataset)
    fingerprint = {m["producerFingerprint"] for m in members}

    conflict = api.run(pipeline_type, dataset, params_for(run_id, config=changed))
    assert conflict["status"] == "failed"
    assert api.tasks(conflict["pipelineRunId"])[register_task]["status"] == "failed"
    assert _without_timestamps(getattr(api, listing)(dataset)) == _without_timestamps(members), (
        "a refused replacement must leave canonical membership untouched"
    )

    replaced = api.run(pipeline_type, dataset, params_for(run_id, config=changed, replace=True))
    assert replaced["status"] == "succeeded"
    after = getattr(api, listing)(dataset)
    assert {m["producerFingerprint"] for m in after}.isdisjoint(fingerprint)
    assert len({m["producerFingerprint"] for m in after}) == 1


def test_a_blocked_pipeline_resumes_at_the_blocked_task(api, baseline_run):
    dataset = _dataset(api, "blocked")
    params = scene_params(
        baseline_run["robot_run_id"],
        validate_scene={"require_target_channels": ["NONEXISTENT_CHANNEL"]},
    )
    created = api.create_run("recording_scene_building", dataset, params)
    run_id = created["pipelineRunId"]

    api.dispatched(run_id)
    assert api.wait(run_id)["status"] == "blocked"

    def finished_tasks() -> dict:
        tasks = api.tasks(run_id)
        return {
            t: (tasks[t]["pipelineTaskRunId"], tasks[t]["jobId"], tasks[t]["status"])
            for t in ("build_recording_scenes", "register_scenes")
        }

    before = finished_tasks()
    assert {status for _, _, status in before.values()} == {"succeeded"}

    api.dispatched(run_id)  # a BLOCKED run must be redispatchable
    assert api.wait(run_id)["status"] == "blocked", "the same params block again"
    assert finished_tasks() == before, "succeeded tasks must not be re-executed on retry"


def test_a_failed_pipeline_is_redispatchable_and_registers_nothing(api):
    dataset = _dataset(api, "failed")
    created = api.create_run(
        "recording_scene_building", dataset, scene_params("run-that-was-never-registered")
    )
    run_id = created["pipelineRunId"]

    api.dispatched(run_id)
    first = api.wait(run_id)
    assert first["status"] == "failed"
    build = api.tasks(run_id)["build_recording_scenes"]
    assert build["status"] == "failed" and build["error"]
    assert api.scenes(dataset) == []

    api.dispatched(run_id)  # a FAILED run must be redispatchable
    assert api.wait(run_id)["status"] == "failed"
    assert api.scenes(dataset) == []


def _concurrent_runs(api, dataset, params) -> list[dict]:
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(
            pool.map(lambda _: api.run("recording_scene_building", dataset, params), range(3))
        )


def test_concurrent_runs_over_one_scope_converge_on_one_canonical_membership(api, baseline_run):
    """Three concurrent runs register one membership, twice over: the first
    registration of an empty scope, then an explicit replacement of what the first
    round registered (the changed configuration), so every run races something both
    times."""
    dataset = _dataset(api, "concurrent")
    run_id = baseline_run["robot_run_id"]
    changed = SCOPES["scene"][4]
    assert api.scenes(dataset) == [], "the disposable runtime starts without Scenes"

    first_round = _concurrent_runs(api, dataset, scene_params(run_id))
    assert [r["status"] for r in first_round] == ["succeeded"] * 3
    registered = api.scenes(dataset)
    assert len({s["producerFingerprint"] for s in registered}) == 1
    assert len(registered) == baseline_run["scene_count"], (
        "one RobotRun builds exactly the Scenes the baseline registered for it"
    )

    second_round = _concurrent_runs(
        api, dataset, scene_params(run_id, config=changed, replace=True)
    )
    assert [r["status"] for r in second_round] == ["succeeded"] * 3
    replaced = api.scenes(dataset)
    assert len({s["producerFingerprint"] for s in replaced}) == 1
    assert {s["producerFingerprint"] for s in replaced}.isdisjoint(
        {s["producerFingerprint"] for s in registered}
    )

    summary = api.get(f"/datasets/{dataset[0]}/versions/{dataset[1]}")["version"]
    assert summary["scene"]["sceneCount"] == len(replaced), (
        "the registrar-owned DatasetVersion summary equals the registered membership"
    )


def test_every_pipeline_task_runs_as_a_job_on_the_job_backend(api, baseline_run):
    """The orchestrator submits each task's Job to the job backend and never runs it:
    the run is dispatched once to the orchestrator, and every task that ran has a
    Job of the run with exactly one job dispatch of its own."""
    dataset = _dataset(api, "orchestrator")
    run = api.run("recording_scene_building", dataset, scene_params(baseline_run["robot_run_id"]))
    assert run["status"] == "succeeded"
    run_id = run["pipelineRunId"]

    def dispatches(resource_id: str) -> list[tuple[str, str]]:
        executions = api.get("/executions", resource_id=resource_id)["executions"]
        return [(e["executionKind"], e["executionBackend"]) for e in executions]

    assert dispatches(run_id) == [("pipeline_run", "celery")]

    executed = [t for t in api.tasks(run_id).values() if t["status"] != "skipped"]
    assert executed
    for task in executed:
        job = api.get(f"/jobs/{task['jobId']}")["job"]
        assert job["status"] == "succeeded", job
        assert (job["pipelineRunId"], job["pipelineTaskRunId"]) == (
            run_id,
            task["pipelineTaskRunId"],
        )
        assert dispatches(task["jobId"]) == [("job_run", "celery")]
