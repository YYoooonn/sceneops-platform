"""Pipeline execution contracts of the Build -> Register -> Compose lifecycle: dedup,
force, convergence, additive revisions, immutable composition, blocked resumption,
failure recovery, concurrent registration and composition, and that every task of a
pipeline runs as a Job through the job execution backend.

Each test builds one baseline RobotRun's Scenes or Episodes. Registration is additive
and shared, so the revisions of that RobotRun are the same for every test that builds
it with the same configuration; each test composes DatasetVersions of its own under
the fixed Dataset `sceneops-test-infra-pipelines`, named after the test. The suite runs
on a disposable execution runtime (DISPOSABLE_ENVIRONMENT,
docs/development/test-matrix.md); the Jobs and PipelineRuns the re-executions append
are dropped with the runtime. Retry semantics are properties of the execution model,
the registrars and the composer, so they are proven here once, on the two canonical
build pipelines, rather than inside every user journey.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from infra_support import (
    EPISODE_BUILD_CONFIG,
    INFRA_PIPELINES_DATASET,
    SCENE_BUILD_CONFIG,
    compose_params,
    episode_params,
    scene_params,
)


def _finished_job(api, job_id: str) -> dict:
    deadline = time.monotonic() + 120
    job = api.get(f"/jobs/{job_id}")["job"]
    while job["status"] not in ("succeeded", "failed") and time.monotonic() < deadline:
        time.sleep(2)
        job = api.get(f"/jobs/{job_id}")["job"]
    return job


def _pins(records: list[dict]) -> list[tuple[str, str, str]]:
    unit = "sceneId" if "sceneId" in records[0] else "episodeId"
    return sorted((r[unit], r["manifestArtifactId"], r["manifestChecksum"]) for r in records)


def _without_timestamps(records: list[dict]) -> list[dict]:
    return sorted(
        ({k: v for k, v in r.items() if k != "registeredAt"} for r in records),
        key=lambda r: (r.get("sceneId") or r.get("episodeId"), r["manifestChecksum"]),
    )


# (pipeline type, params builder, kind, register task, a changed channel selection:
#  the same units, another producer fingerprint)
SCOPES = {
    "scene": (
        "recording_scene_building",
        scene_params,
        "scenes",
        "register_scenes",
        {**SCENE_BUILD_CONFIG, "channels": SCENE_BUILD_CONFIG["channels"][:1]},
    ),
    "episode": (
        "recording_episode_building",
        episode_params,
        "episodes",
        "register_episodes",
        {
            **EPISODE_BUILD_CONFIG,
            "streams": [
                s for s in EPISODE_BUILD_CONFIG["streams"] if s["topic"] != "/vehicle/status"
            ],
        },
    ),
}


@pytest.fixture(scope="module")
def dataset(api) -> str:
    return api.dataset(INFRA_PIPELINES_DATASET)


def test_a_recording_run_names_no_dataset_version(api, baseline_run):
    response = api.post(
        "/pipelines/runs",
        {
            "type": "recording_scene_building",
            "dataset_id": INFRA_PIPELINES_DATASET,
            "dataset_version": "refused",
            "params": scene_params(baseline_run["robot_run_id"]),
        },
    )
    assert response.status_code == 400, response.text
    assert "executes in no DatasetVersion" in response.text


def test_identical_requests_dedup_and_force_creates_a_fresh_run(api, baseline_run):
    run_id = baseline_run["robot_run_id"]
    params = scene_params(run_id)

    first = api.create_run("recording_scene_building", params)
    again = api.create_run("recording_scene_building", params)
    forced = api.create_run("recording_scene_building", params, force=True)
    other_params = api.create_run(
        "recording_scene_building", scene_params(run_id, config=SCOPES["scene"][4])
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
    # force never starts a second in-flight Job of one key, so it waits for this one.
    assert _finished_job(api, first)["status"] == "succeeded"

    forced = api.post(
        "/jobs",
        {
            "type": "register_robot_run",
            "force": True,
            "params": {"manifest_uri": manifest_uri},
        },
        expect=201,
    ).json()["job"]["jobId"]
    assert forced != first
    api.post(f"/jobs/{forced}/execute", {})
    job = _finished_job(api, forced)

    assert job["status"] == "succeeded", job
    assert job["result"]["created"] is False
    assert api.get(f"/robot-runs/{run_id}")["robotRun"] == run_before
    assert (
        api.get("/artifacts", owner_type="robot_run", owner_id=run_id, limit=500)["artifacts"]
        == artifacts_before
    )


def test_identical_jobs_dedup_and_force_creates_a_fresh_job_once_finished(api):
    body = {
        "type": "export_analytics_snapshot",
        "dataset_id": INFRA_PIPELINES_DATASET,
        "dataset_version": "jobs-dedup",
        "params": {},
    }
    first = api.post("/jobs", body, expect=201).json()["job"]["jobId"]
    again = api.post("/jobs", body, expect=201).json()["job"]["jobId"]
    forced_in_flight = api.post("/jobs", {**body, "force": True}, expect=201).json()["job"][
        "jobId"
    ]

    assert again == first
    assert forced_in_flight == first, "one Job of a key is in flight at a time"

    api.post(f"/jobs/{first}/execute", {})
    assert _finished_job(api, first)["status"] in ("succeeded", "failed")
    forced = api.post("/jobs", {**body, "force": True}, expect=201).json()["job"]["jobId"]

    assert forced != first


@pytest.mark.parametrize("scope", ["scene", "episode"])
def test_rebuilding_an_unchanged_scope_converges(api, baseline_run, scope):
    pipeline_type, params_for, kind, register_task, _ = SCOPES[scope]
    run_id = baseline_run["robot_run_id"]
    params = params_for(run_id)

    first = api.run(pipeline_type, params, force=False)
    assert first["status"] == "succeeded"
    before = api.revisions(kind, run_id)
    assert before

    second = api.run(pipeline_type, params)
    assert second["status"] == "succeeded"
    registered = api.tasks(second["pipelineRunId"])[register_task]["result"]

    assert len(registered["summary"][f"existing_revision_{scope}_ids"]) == len(
        registered["refs"]["revisions"]
    )
    assert registered["summary"][f"new_unit_{scope}_ids"] == []
    assert _without_timestamps(api.revisions(kind, run_id)) == _without_timestamps(before)
    assert api.tasks(second["pipelineRunId"])["compose_dataset_version"]["status"] == "skipped"


@pytest.mark.parametrize("scope", ["scene", "episode"])
def test_a_changed_configuration_adds_revisions_and_leaves_composed_versions_unchanged(
    api, baseline_run, dataset, scope
):
    """Acceptance criterion: a changed channel selection adds one revision per unit
    and leaves the earlier revision, and every DatasetVersion that pins it, as it
    was. A DatasetVersion name is bound once: composing it again with the same pins
    converges, with other pins it fails and changes nothing."""
    pipeline_type, params_for, kind, register_task, changed = SCOPES[scope]
    run_id = baseline_run["robot_run_id"]
    original_name, changed_name = f"additive-{scope}-original", f"additive-{scope}-changed"

    original = api.run(
        pipeline_type, params_for(run_id, compose=compose_params(dataset, original_name))
    )
    assert original["status"] == "succeeded", original
    pinned = api.members(kind, dataset, original_name)
    version = api.version(dataset, original_name)

    added = api.run(
        pipeline_type,
        params_for(run_id, config=changed, compose=compose_params(dataset, changed_name)),
    )
    assert added["status"] == "succeeded", added
    registered = api.tasks(added["pipelineRunId"])[register_task]["result"]["summary"]
    assert registered[f"new_unit_{scope}_ids"] == []
    assert len(registered[f"new_revision_{scope}_ids"]) == len(pinned)

    repinned = api.members(kind, dataset, changed_name)
    unit = "sceneId" if scope == "scene" else "episodeId"
    assert sorted(r[unit] for r in repinned) == sorted(r[unit] for r in pinned)
    assert {r["producerFingerprint"] for r in repinned}.isdisjoint(
        {r["producerFingerprint"] for r in pinned}
    )
    for record in pinned:
        revisions = api.get(f"/{kind}/{record[unit]}")["revisions"]
        assert {r["manifestChecksum"] for r in revisions} >= {record["manifestChecksum"]}
    assert _pins(api.members(kind, dataset, original_name)) == _pins(pinned)
    assert api.version(dataset, original_name) == version

    # The same name and pins: the composition converges.
    again = api.run(
        pipeline_type, params_for(run_id, compose=compose_params(dataset, original_name))
    )
    assert again["status"] == "succeeded", again
    composed = api.tasks(again["pipelineRunId"])["compose_dataset_version"]["result"]
    assert composed["summary"]["created"] is False
    assert composed["refs"]["composition_checksum"] == version["compositionChecksum"]

    # The same name, other pins: refused, and the version keeps its composition.
    rebind = api.run(
        pipeline_type,
        params_for(run_id, config=changed, compose=compose_params(dataset, original_name)),
    )
    assert rebind["status"] == "failed"
    tasks = api.tasks(rebind["pipelineRunId"])
    assert tasks["compose_dataset_version"]["status"] == "failed"
    assert "never rebound" in tasks["compose_dataset_version"]["error"]["message"]
    assert tasks[f"validate_{scope}"]["status"] == "pending"
    assert api.version(dataset, original_name) == version
    assert _pins(api.members(kind, dataset, original_name)) == _pins(pinned)


def test_a_composition_mixes_domains_from_the_register_pins(api, baseline_run, dataset):
    """Pins the register stages returned compose one version of both domains through
    the standalone Job; the version holds exactly them."""
    run_id = baseline_run["robot_run_id"]
    pins = []
    for scope in ("scene", "episode"):
        pipeline_type, params_for, _, register_task, _ = SCOPES[scope]
        run = api.run(pipeline_type, params_for(run_id), force=False)
        assert run["status"] == "succeeded"
        pins += api.tasks(run["pipelineRunId"])[register_task]["result"]["refs"]["revisions"]

    name = "mixed-domains"
    body = {
        "type": "compose_dataset_version",
        "params": {"dataset_id": dataset, "dataset_version": name, "pins": pins},
    }
    job_id = api.post("/jobs", body, expect=201).json()["job"]["jobId"]
    api.post(f"/jobs/{job_id}/execute", {})
    job = _finished_job(api, job_id)
    assert job["status"] == "succeeded", job
    assert (job["datasetId"], job["datasetVersion"]) == (dataset, name)

    members = api.members("scenes", dataset, name) + api.members("episodes", dataset, name)
    assert sorted(
        (m.get("sceneId") or m.get("episodeId"), m["manifestChecksum"]) for m in members
    ) == sorted((p["unit_id"], p["manifest_checksum"]) for p in pins)
    version = api.version(dataset, name)
    assert version["scene"]["sceneCount"] == sum(p["domain"] == "scene" for p in pins)
    assert version["episode"]["episodeCount"] == sum(p["domain"] == "episode" for p in pins)


def test_a_blocked_pipeline_resumes_at_the_blocked_task(api, baseline_run):
    params = scene_params(
        baseline_run["robot_run_id"],
        validate_scene={"require_target_channels": ["NONEXISTENT_CHANNEL"]},
    )
    created = api.create_run("recording_scene_building", params)
    run_id = created["pipelineRunId"]

    api.dispatched(run_id)
    assert api.wait(run_id)["status"] == "blocked"

    def finished_tasks() -> dict:
        tasks = api.tasks(run_id)
        return {
            t: (tasks[t]["pipelineTaskRunId"], tasks[t]["jobId"], tasks[t]["status"])
            for t in ("build_recording_scenes", "register_scenes", "compose_dataset_version")
        }

    before = finished_tasks()
    assert [status for _, _, status in before.values()] == ["succeeded", "succeeded", "skipped"]

    api.dispatched(run_id)  # a BLOCKED run must be redispatchable
    assert api.wait(run_id)["status"] == "blocked", "the same params block again"
    assert finished_tasks() == before, "finished tasks must not be re-executed on retry"


def test_a_failed_pipeline_is_redispatchable_and_registers_nothing(api, dataset):
    never_registered = "run-that-was-never-registered"
    name = "failed"
    created = api.create_run(
        "recording_scene_building",
        scene_params(never_registered, compose=compose_params(dataset, name)),
    )
    run_id = created["pipelineRunId"]

    api.dispatched(run_id)
    first = api.wait(run_id)
    assert first["status"] == "failed"
    build = api.tasks(run_id)["build_recording_scenes"]
    assert build["status"] == "failed" and build["error"]
    assert api.revisions("scenes", never_registered) == []
    assert api.version(dataset, name) is None

    api.dispatched(run_id)  # a FAILED run must be redispatchable
    assert api.wait(run_id)["status"] == "failed"
    assert api.revisions("scenes", never_registered) == []
    assert api.version(dataset, name) is None


def _concurrent_runs(api, params) -> list[dict]:
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(
            pool.map(lambda _: api.run("recording_scene_building", params), range(3))
        )


def test_concurrent_runs_converge_on_one_set_of_revisions_and_one_composition(
    api, baseline_run, dataset
):
    """Three concurrent runs register the same revisions and compose the same name:
    registration converges on one row per revision and exactly one composition
    commits, the others converge on it. A second round with a changed configuration
    races new revisions of the same units into another version, and leaves the first
    version as it was."""
    run_id = baseline_run["robot_run_id"]
    changed = SCOPES["scene"][4]

    first_round = _concurrent_runs(
        api, scene_params(run_id, compose=compose_params(dataset, "concurrent-1"))
    )
    assert [r["status"] for r in first_round] == ["succeeded"] * 3
    created = [
        api.tasks(r["pipelineRunId"])["compose_dataset_version"]["result"]["summary"]["created"]
        for r in first_round
    ]
    assert sorted(created) == [False, False, True]
    composed = api.members("scenes", dataset, "concurrent-1")
    assert len({s["producerFingerprint"] for s in composed}) == 1
    assert len(composed) == baseline_run["scene_count"], (
        "one RobotRun builds exactly the Scenes the baseline registered for it"
    )

    second_round = _concurrent_runs(
        api,
        scene_params(run_id, config=changed, compose=compose_params(dataset, "concurrent-2")),
    )
    assert [r["status"] for r in second_round] == ["succeeded"] * 3
    recomposed = api.members("scenes", dataset, "concurrent-2")
    assert len({s["producerFingerprint"] for s in recomposed}) == 1
    assert {s["producerFingerprint"] for s in recomposed}.isdisjoint(
        {s["producerFingerprint"] for s in composed}
    )
    assert _pins(api.members("scenes", dataset, "concurrent-1")) == _pins(composed)

    for name, members in (("concurrent-1", composed), ("concurrent-2", recomposed)):
        summary = api.version(dataset, name)
        assert summary["scene"]["sceneCount"] == len(members), (
            "the summary is computed once, from the composed members"
        )


def test_every_pipeline_task_runs_as_a_job_on_the_job_backend(api, baseline_run, dataset):
    """The orchestrator submits each task's Job to the job backend and never runs it:
    the run is dispatched once to the orchestrator, and every task that ran has a
    Job of the run with exactly one job dispatch of its own."""
    run = api.run(
        "recording_scene_building",
        scene_params(baseline_run["robot_run_id"], compose=compose_params(dataset, "orchestrator")),
    )
    assert run["status"] == "succeeded"
    run_id = run["pipelineRunId"]

    def dispatches(resource_id: str) -> list[tuple[str, str]]:
        executions = api.get("/executions", resource_id=resource_id)["executions"]
        return [(e["executionKind"], e["executionBackend"]) for e in executions]

    assert dispatches(run_id) == [("pipeline_run", "celery")]

    executed = [t for t in api.tasks(run_id).values() if t["status"] != "skipped"]
    assert {t["pipelineTaskId"] for t in executed} >= {
        "build_recording_scenes",
        "register_scenes",
        "compose_dataset_version",
        "validate_scene",
    }
    for task in executed:
        job = api.get(f"/jobs/{task['jobId']}")["job"]
        assert job["status"] == "succeeded", job
        assert (job["pipelineRunId"], job["pipelineTaskRunId"]) == (
            run_id,
            task["pipelineTaskRunId"],
        )
        assert dispatches(task["jobId"]) == [("job_run", "celery")]
