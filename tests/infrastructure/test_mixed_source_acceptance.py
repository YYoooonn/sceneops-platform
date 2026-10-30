"""Mixed-source DatasetVersions on a disposable runtime
(`make test-infrastructure SUITE=mixed-sources`; ADR-015 §3, §4, §7, §12; M3 S5).

One DatasetVersion pins the Recording Import RobotRun of scene-0061 (its Scene and
its Episode, registered by the suite's baseline), the nuScenes scene-0061 slice's
Scene and the 206 pusht Episodes, each built and registered by its own pipeline on
the runtime's Celery workers. The version is composed by pins and by selector and
held to the independent mixed-source verifier
(tools/reference/mixed_source_verify.py, a separate read-only process), whose
expectations come from the locked recording and the raw trees only; each part is
also held to its own source verifier. Around it: validation and profiling of mixed
members in one Job, the consumers that resolve or refuse a mixed version, execution
provenance through a second declaration of each content, idempotent re-runs of every
S1–S4 step, and concurrent compositions of one name across worker processes.

Prerequisites: the nuScenes v1.0-mini tree at data/raw/nuscenes, the pinned pusht
tree (`make reference-source-fetch`) and the locked scene-0061 recording
(`make reference-data-bootstrap`). The make target sets FIXTURE=scene-0061, so the
baseline composes `baseline-scene-0061` for the recording verifier. The measurements
are printed as one ``mixed_source_measurements`` JSON line.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from infra_support import (
    EPISODE_BUILD_CONFIG,
    JOB_TERMINAL,
    REPO_ROOT,
    SCENE_BUILD_CONFIG,
    Api,
)
from sceneops_core.artifacts.schemas.enums import ArtifactBackend
from sceneops_core.config import StorageSettings
from sceneops_core.provenance import manifest_artifact_id_for
from sceneops_storage.backends.s3 import S3ArtifactStore

SNAPSHOTS = json.loads((REPO_ROOT / "config/reference/source-snapshots.json").read_text())["snapshots"]
SPEC = json.loads((REPO_ROOT / "config/reference/mixed-sources/composition.json").read_text())
EXPECTED = json.loads((REPO_ROOT / "config/reference/mixed-sources/expectations.json").read_text())
PARTS = {p["part"]: p for p in SPEC["parts"]}
DATASET = "sceneops-test-mixed-sources"
# The episode-learning journey's alignment of the scene-0061 Episode.
ALIGNMENT = {
    "target_frequency_hz": 5.0, "tolerance_us": 10_000_000, "max_gap_us": 10_000_000,
    "channel_policies": {
        f"/vehicle/control#{field}": {"policy": "nearest", "tolerance_us": 10_000_000}
        for field in ("steering", "throttle", "brake")
    },
}  # fmt: skip
MEASUREMENTS: dict[str, float | int] = {}

pytestmark = pytest.mark.skipif(
    not (
        (REPO_ROOT / SNAPSHOTS["nuscenes-scene-0061"]["local_root"] / "v1.0-mini").is_dir()
        and (REPO_ROOT / SNAPSHOTS["lerobot-pusht"]["local_root"]).is_dir()
    ),
    reason="data/raw/nuscenes or data/raw/lerobot/pusht is missing",
)


# ── processes, the control plane and the stores ──────────────────────────────


def _publish(snapshot: str, revision: str) -> dict:
    declaration = SNAPSHOTS[snapshot]
    root = os.environ["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"]
    env = {
        **os.environ,
        "SCENEOPS_PUBLISHER_ARTIFACT__BACKEND": "minio",
        "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI": root,
        "SCENEOPS_PUBLISHER_ARTIFACT__ENDPOINT_URL": os.environ["MINIO_ENDPOINT_URL"],
        "SCENEOPS_PUBLISHER_ARTIFACT__ACCESS_KEY_ID": os.environ["MINIO_ROOT_USER"],
        "SCENEOPS_PUBLISHER_ARTIFACT__SECRET_ACCESS_KEY": os.environ["MINIO_ROOT_PASSWORD"],
    }
    command = [
        sys.executable, "-m", "sceneops_publisher", "publish-snapshot",
        "--source-dir", str(REPO_ROOT / declaration["local_root"]), "--format", declaration["format"],
        "--upstream", declaration["upstream"], "--upstream-revision", revision,
    ]  # fmt: skip
    for key, value in declaration.get("annotations", {}).items():
        command += ["--annotation", f"{key}={value}"]
    for pattern in declaration["include"]:
        command += ["--include", pattern]
    completed = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout)


def _submit(api: Api, job_type: str, params: dict) -> str:
    created = api.post("/jobs", {"type": job_type, "params": params, "force": True})
    assert created.status_code == 201, created.text
    job = created.json()["job"]
    if job["status"] not in JOB_TERMINAL:
        executed = api.post(f"/jobs/{job['jobId']}/execute", {})
        assert executed.status_code == 200, executed.text
    return job["jobId"]


def _wait(api: Api, job_id: str, timeout: float = 900.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = api.get(f"/jobs/{job_id}")["job"]
        if job["status"] in JOB_TERMINAL:
            return job
        time.sleep(1)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def _job(api: Api, job_type: str, params: dict) -> dict:
    return _wait(api, _submit(api, job_type, params))


def _succeeded(api: Api, job_type: str, params: dict) -> dict:
    job = _job(api, job_type, params)
    assert job["status"] == "succeeded", job.get("error") or job
    return job["result"]


def _snapshot(api: Api, snapshot: str, revision: str) -> dict:
    published = _publish(snapshot, revision)
    return _succeeded(api, "register_source_snapshot", {"descriptor_uri": published["descriptor_uri"]})


def _task_result(api: Api, task: dict) -> dict:
    return api.get(f"/jobs/{task['jobId']}")["job"]["result"]


def _store() -> S3ArtifactStore:
    return S3ArtifactStore(
        settings=StorageSettings(
            backend=ArtifactBackend.MINIO,
            root_uri=os.environ["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"],
            endpoint_url=os.environ["MINIO_ENDPOINT_URL"],
            access_key_id=os.environ["MINIO_ROOT_USER"],
            secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
        )
    )


def _objects() -> dict[str, int]:
    listed = asyncio.run(_store().list_objects(os.environ["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"]))
    return {o.uri: o.size_bytes for o in listed}


# The canonical and source tables, and the artifacts: everything an S1–S4 step or
# a composition writes. Jobs, PipelineRuns and job-keyed reports are execution
# history the platform appends by design and are not compared.
DOMAIN_TABLES = (
    "robot_runs", "source_contents", "source_snapshots", "scenes", "scene_revisions",
    "episodes", "episode_revisions", "dataset_versions", "dataset_version_members", "artifacts",
)  # fmt: skip


def _rows() -> dict[str, tuple[int, str]]:
    """Per table: its row count and a digest of every row's full text."""

    async def query() -> dict[str, tuple[int, str]]:
        engine = create_async_engine(os.environ["SCENEOPS_DATABASE_URL"])
        try:
            async with engine.connect() as connection:
                return {
                    table: tuple(
                        (
                            await connection.execute(
                                text(f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {table} t")
                            )
                        ).one()
                    )
                    for table in DOMAIN_TABLES
                }  # fmt: skip
        finally:
            await engine.dispose()

    return asyncio.run(query())


def _verify(tool: str, *args: str) -> dict:
    """An independent verifier as its own read-only process."""
    command = [sys.executable, "-I", str(REPO_ROOT / "tools/reference" / tool), *args]
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, check=False, cwd=REPO_ROOT)
    MEASUREMENTS[f"verify_seconds.{tool}.{args[-1].rsplit('/', 1)[-1]}"] = round(time.monotonic() - started, 1)
    report = json.loads(completed.stdout)
    assert completed.returncode == 0 and report["ok"], (
        report["violations"][:5], report["undetected_controls"], completed.stderr[-2000:],
    )  # fmt: skip
    assert report["negative_controls"] and all(c["detected"] for c in report["negative_controls"])
    return report


def _pin(domain: str, revision: dict) -> dict:
    """A RevisionPin (snake_case Job params) of an API revision record."""
    return {
        "domain": domain,
        "unit_id": revision[f"{domain}Id"],
        "manifest_artifact_id": revision["manifestArtifactId"],
        "manifest_checksum": revision["manifestChecksum"],
    }


# ── fixtures: the three sources, built and registered ───────────────────────────


@pytest.fixture(scope="module")
def recording(api: Api, baseline_run: dict) -> dict:
    """The RobotRun of scene-0061 the baseline registered, with its revisions."""
    part = PARTS["recording-scene-0061"]
    assert baseline_run["source_unit"] == part["fixture"]
    run_id = baseline_run["robot_run_id"]
    assert run_id == EXPECTED["parts"]["recording-scene-0061"]["source_id"]
    scenes = api.revisions("scenes", run_id)
    episodes = api.revisions("episodes", run_id)
    return {
        "robot_run_id": run_id,
        "pins": [_pin("scene", r) for r in scenes] + [_pin("episode", r) for r in episodes],
        "fingerprints": {
            "scene": {r["producerFingerprint"] for r in scenes},
            "episode": {r["producerFingerprint"] for r in episodes},
        },
    }


def _content(api: Api, part: str, pipeline: str, build_task: str, register_task: str) -> dict:
    spec = PARTS[part]
    started = time.monotonic()
    snapshot = _snapshot(api, spec["snapshot"], SNAPSHOTS[spec["snapshot"]]["upstream_revision"])
    MEASUREMENTS[f"publish_and_register_seconds.{part}"] = round(time.monotonic() - started, 1)
    params: dict = {
        "snapshot_id": snapshot["snapshot_id"],
        "build_config": json.loads((REPO_ROOT / spec["build_config"]).read_text()),
    }
    if spec["unit_keys"] is not None:
        params["unit_keys"] = spec["unit_keys"]
    started = time.monotonic()
    run = api.run(pipeline, {build_task: params})
    MEASUREMENTS[f"pipeline_seconds.{part}"] = round(time.monotonic() - started, 1)
    assert run["status"] == "succeeded", run
    tasks = api.tasks(run["pipelineRunId"])
    build, register = _task_result(api, tasks[build_task]), _task_result(api, tasks[register_task])
    assert (register["source_kind"], register["source_id"]) == ("content", snapshot["content_id"])
    return {"snapshot": snapshot, "params": params, "tasks": tasks, "build": build, "pins": register["revisions"]}


@pytest.fixture(scope="module")
def nuscenes(api: Api) -> dict:
    return _content(api, "nuscenes-scene-0061", "snapshot_scene_building", "build_snapshot_scenes", "register_scenes")


@pytest.fixture(scope="module")
def pusht(api: Api) -> dict:
    return _content(api, "lerobot-pusht", "snapshot_episode_building", "build_snapshot_episodes", "register_episodes")


@pytest.fixture(scope="module")
def pins(recording: dict, nuscenes: dict, pusht: dict) -> list[dict]:
    return recording["pins"] + nuscenes["pins"] + pusht["pins"]


def _selector(recording: dict, nuscenes: dict, pusht: dict) -> dict:
    (scene_fp,), (episode_fp,) = recording["fingerprints"]["scene"], recording["fingerprints"]["episode"]
    clauses = [
        ("scene", "recording", recording["robot_run_id"], scene_fp),
        ("episode", "recording", recording["robot_run_id"], episode_fp),
        ("scene", "content", nuscenes["snapshot"]["content_id"], nuscenes["build"]["producer_fingerprint"]),
        ("episode", "content", pusht["snapshot"]["content_id"], pusht["build"]["producer_fingerprint"]),
    ]  # fmt: skip
    keys = ("domain", "source_kind", "source_id", "producer_fingerprint")
    return {"sources": [dict(zip(keys, c)) for c in clauses]}


@pytest.fixture(scope="module")
def by_pins(api: Api, pins: list[dict]) -> dict:
    api.dataset(DATASET)
    started = time.monotonic()
    result = _succeeded(api, "compose_dataset_version", {"dataset_id": DATASET, "dataset_version": "pins", "pins": pins})
    MEASUREMENTS["compose_seconds.pins"] = round(time.monotonic() - started, 1)
    return result


@pytest.fixture(scope="module")
def by_selector(api: Api, by_pins: dict, recording: dict, nuscenes: dict, pusht: dict) -> dict:
    return _succeeded(
        api, "compose_dataset_version",
        {"dataset_id": DATASET, "dataset_version": "selector", "selector": _selector(recording, nuscenes, pusht)},
    )  # fmt: skip


# ── composition ─────────────────────────────────────────────────────────────────


def test_the_mixed_version_by_pins_is_the_raw_derivation(api: Api, pins: list[dict], by_pins: dict) -> None:
    summary = EXPECTED["summary"]
    assert (by_pins["created"], by_pins["member_count"]) == (True, len(pins))
    assert (by_pins["scene_count"], by_pins["episode_count"]) == (summary["scene_count"], summary["episode_count"])
    version = api.version(DATASET, "pins")
    assert version["selector"] is None
    assert version["scene"]["observedChannels"] == summary["observed_channels"]
    # Both domains and both source kinds, as the members report them.
    scenes, episodes = api.members("scenes", DATASET, "pins"), api.members("episodes", DATASET, "pins")
    assert sorted(r["sourceKind"] for r in scenes) == ["content", "recording"]
    assert sorted({r["sourceKind"] for r in episodes}) == ["content", "recording"]
    assert all(r["robotRunId"] is None for r in scenes + episodes if r["sourceKind"] == "content")
    report = _verify("mixed_source_verify.py", "verify", "--dataset", f"{DATASET}/pins")
    MEASUREMENTS["mixed_verify_checks.pins"] = report["check_count"]
    MEASUREMENTS["mixed_verify_controls.pins"] = len(report["negative_controls"])


def test_the_selector_composes_the_same_version(api: Api, by_pins: dict, by_selector: dict) -> None:
    assert by_selector["composition_checksum"] == by_pins["composition_checksum"]
    version = api.version(DATASET, "selector")
    assert len(version["selector"]["sources"]) == 4
    report = _verify("mixed_source_verify.py", "verify", "--dataset", f"{DATASET}/selector")
    assert report["composed_by"] == "selector" and report["checks"].get("selector") == 1


def test_every_part_also_passes_its_own_source_verifier(api: Api, by_pins: dict, nuscenes: dict, pusht: dict) -> None:
    """The per-source verifiers hold each part's payloads and preserved files,
    which the mixed verifier does not read."""
    _verify("nuscenes_scene_verify.py", "--unit-key", PARTS["nuscenes-scene-0061"]["unit_keys"][0],
            "verify", "--snapshot-id", nuscenes["snapshot"]["snapshot_id"])  # fmt: skip
    _verify("lerobot_verify.py", "verify", "--snapshot-id", pusht["snapshot"]["snapshot_id"])
    _verify("reference_verify.py", "verify", "--mode", "recording_import", "--fixture", "scene-0061")


def test_validation_and_profiling_take_mixed_sources_in_one_job(api: Api, recording: dict, nuscenes: dict, pusht: dict, by_pins: dict) -> None:
    scene_pins = [p for p in recording["pins"] if p["domain"] == "scene"] + nuscenes["pins"]
    episode_pins = [p for p in recording["pins"] if p["domain"] == "episode"] + pusht["pins"]
    validated = _succeeded(api, "validate_scene", {"revisions": scene_pins})
    assert (validated["status"], validated["checked_scene_count"]) == ("ready", 2)
    profiled = _succeeded(api, "profile_scene", {"revisions": scene_pins})
    assert profiled["observation_count"] == EXPECTED["summary"]["observation_count"]
    validated = _succeeded(api, "validate_episode", {"revisions": episode_pins})
    assert (validated["status"], validated["checked_episode_count"]) == ("ready", 207)
    profiled = _succeeded(api, "profile_episode", {"revisions": episode_pins})
    assert profiled["checked_episode_count"] == 207 and profiled["transition_topics"]
    quality = api.get(f"/datasets/{DATASET}/versions/pins/quality")
    assert quality["counts"]["sceneCount"] == 2
    assert (quality["readiness"], quality["validation"]["readySceneCount"]) == ("ready", 2)


def test_consumers_resolve_or_refuse_a_mixed_version_explicitly(api: Api, by_pins: dict, recording: dict, pusht: dict) -> None:
    before = _objects()
    # Sample views read observations only: the nuScenes Scene gets a view, the
    # RobotRun Scene (no LIDAR_TOP channel) is skipped with its reason.
    views = _succeeded(
        api, "build_scene_sample_views",
        {"dataset_id": DATASET, "dataset_version": "pins",
         "policy": {"anchor": {"channel": "LIDAR_TOP"}, "members": [{"channel": "CAM_FRONT", "tolerance_ns": 50_000_000}]}},
    )  # fmt: skip
    (recording_scene,) = [p for p in recording["pins"] if p["domain"] == "scene"]
    assert views["scene_count"] == 1
    assert views["skipped"] == [{"reason": "scene_lacks_anchor_channel", "scene_id": recording_scene["unit_id"]}]
    # The analytical observation table exports every Scene's payloads by
    # reference, preserved files and recording payloads alike.
    export = _succeeded(api, "export_analytics_snapshot", {"dataset_id": DATASET, "dataset_version": "pins"})
    assert export["row_counts"]["scenes"] == 2
    assert export["row_counts"]["observations"] == EXPECTED["summary"]["observation_count"]
    # Alignment refuses a pusht member and aligns the RobotRun Episode of the same version.
    members = {m["episodeId"]: m for m in api.members("episodes", DATASET, "pins")}

    def align(member: dict) -> dict:
        return _job(api, "align_episode", {
            "episodes": [{"episode_id": member["episodeId"], "source_artifact_id": member["manifestArtifactId"],
                          "source_manifest_sha256": member["manifestChecksum"].removeprefix("sha256:")}],
            "dataset_id": DATASET, "dataset_version": "pins", "alignment_config": ALIGNMENT,
        })  # fmt: skip

    refused = align(members[pusht["pins"][0]["unit_id"]])
    assert refused["status"] == "failed" and "transition streams" in json.dumps(refused["error"])
    (recording_episode,) = [p for p in recording["pins"] if p["domain"] == "episode"]
    assert align(members[recording_episode["unit_id"]])["status"] == "succeeded"
    assert set(before) <= set(_objects())


# ── source identity and execution provenance ─────────────────────────────────────


def test_external_content_creates_no_robot_run(api: Api, recording: dict, by_pins: dict) -> None:
    runs = api.get("/robot-runs", limit=500)["robotRuns"]
    assert [r["runId"] for r in runs] == [recording["robot_run_id"]]
    for kind in ("scenes", "episodes"):
        units = api.get(f"/{kind}", limit=500)[kind]
        assert {u["sourceKind"] for u in units if u["robotRunId"] is None} == {"content"}


@pytest.mark.parametrize(("part", "fixture_name", "build_type", "register_type"), [
    ("nuscenes-scene-0061", "nuscenes", "build_snapshot_scenes", "register_scenes"),
    ("lerobot-pusht", "pusht", "build_snapshot_episodes", "register_episodes"),
])  # fmt: skip
def test_a_build_records_the_snapshot_it_read_and_reuses_every_revision(
    api: Api, request, by_pins: dict, part: str, fixture_name: str, build_type: str, register_type: str
) -> None:
    content = request.getfixturevalue(fixture_name)
    snapshot = PARTS[part]["snapshot"]
    second = _snapshot(api, snapshot, SNAPSHOTS[snapshot]["upstream_revision"] + "-s5")
    assert (second["content_id"], second["content"], second["snapshot"]) == (
        content["snapshot"]["content_id"], "existing", "created",
    )  # fmt: skip
    before = (_rows(), _objects())
    build = _succeeded(api, build_type, {**content["params"], "snapshot_id": second["snapshot_id"]})
    assert (build["snapshot_id"], build["created_manifest_count"]) == (second["snapshot_id"], 0)
    assert build["manifest_artifact_ids"] == content["build"]["manifest_artifact_ids"]
    register = _succeeded(api, register_type, {"manifest_artifact_ids": build["manifest_artifact_ids"]})
    assert register["revisions"] == content["pins"]
    assert (_rows(), _objects()) == before
    # The revision keeps the provenance of the build that first produced it.
    artifact = api.get(f"/artifacts/{build['manifest_artifact_ids'][0]}")["artifact"]
    assert artifact["jobId"] == content["tasks"][build_type]["jobId"]


# ── idempotency and concurrency ───────────────────────────────────────────────────


def test_rerunning_every_s1_to_s4_step_writes_no_row_artifact_or_object(
    api: Api, recording: dict, nuscenes: dict, pusht: dict, by_pins: dict, by_selector: dict, pins: list[dict]
) -> None:
    before = (_rows(), _objects())
    started = time.monotonic()
    for content, part in ((nuscenes, "nuscenes-scene-0061"), (pusht, "lerobot-pusht")):
        snapshot = PARTS[part]["snapshot"]
        again = _snapshot(api, snapshot, SNAPSHOTS[snapshot]["upstream_revision"])
        assert (again["snapshot_id"], again["content"], again["snapshot"]) == (
            content["snapshot"]["snapshot_id"], "existing", "existing",
        )  # fmt: skip
    for content, build_type, register_type in (
        (nuscenes, "build_snapshot_scenes", "register_scenes"),
        (pusht, "build_snapshot_episodes", "register_episodes"),
    ):
        build = _succeeded(api, build_type, content["params"])
        assert build["created_manifest_count"] == 0
        assert _succeeded(api, register_type, {"manifest_artifact_ids": build["manifest_artifact_ids"]})["revisions"] == content["pins"]
    for build_type, register_type, config, domain in (
        ("build_recording_scenes", "register_scenes", SCENE_BUILD_CONFIG, "scene"),
        ("build_recording_episodes", "register_episodes", EPISODE_BUILD_CONFIG, "episode"),
    ):
        build = _succeeded(api, build_type, {"robot_run_id": recording["robot_run_id"], "build_config": config})
        register = _succeeded(api, register_type, {"manifest_artifact_ids": build["manifest_artifact_ids"]})
        assert register["revisions"] == [p for p in recording["pins"] if p["domain"] == domain]
    for version, members in (("pins", {"pins": pins}), ("selector", {"selector": _selector(recording, nuscenes, pusht)})):
        composed = _succeeded(api, "compose_dataset_version", {"dataset_id": DATASET, "dataset_version": version, **members})
        assert (composed["created"], composed["composition_checksum"]) == (False, by_pins["composition_checksum"])
    MEASUREMENTS["rerun_seconds"] = round(time.monotonic() - started, 1)
    assert (_rows(), _objects()) == before


def test_concurrent_compositions_of_one_name_converge_or_conflict(api: Api, pins: list[dict], pusht: dict, by_pins: dict) -> None:
    """Separate Jobs on the runtime's worker processes; the name's unique key
    decides. An identical request while one is in flight returns that Job
    (jobs-and-pipelines.md, request identity), so each request carries its own
    metadata: three Jobs, one composition."""
    params = {"dataset_id": DATASET, "dataset_version": "concurrent", "pins": pins}
    jobs = [_submit(api, "compose_dataset_version", {**params, "metadata": {"request": i}}) for i in range(3)]
    assert len(set(jobs)) == 3
    results = [_wait(api, j) for j in jobs]
    assert [r["status"] for r in results] == ["succeeded"] * 3
    assert sorted(r["result"]["created"] for r in results) == [False, False, True]
    contested = [
        _submit(api, "compose_dataset_version", {"dataset_id": DATASET, "dataset_version": "contested", "pins": members})
        for members in (pins, pusht["pins"])
    ]
    outcomes = [_wait(api, j) for j in contested]
    assert sorted(o["status"] for o in outcomes) == ["failed", "succeeded"]
    (failed,) = [o for o in outcomes if o["status"] == "failed"]
    assert "never rebound" in json.dumps(failed["error"]), failed["error"]
    (won,) = [o for o in outcomes if o["status"] == "succeeded"]
    assert api.version(DATASET, "contested")["compositionChecksum"] == won["result"]["composition_checksum"]


def test_invalid_mixed_compositions_write_nothing(api: Api, recording: dict, nuscenes: dict, pusht: dict, pins: list[dict], by_pins: dict) -> None:
    before = _rows()
    scene_pin = nuscenes["pins"][0]
    episode_checksum = pusht["pins"][0]["manifest_checksum"]
    # A Scene unit pinned at an Episode manifest's checksum, its artifact id
    # recomputed: a self-consistent pin of no registered revision.
    wrong = {**scene_pin, "manifest_checksum": episode_checksum,
             "manifest_artifact_id": manifest_artifact_id_for(unit_id=scene_pin["unit_id"], manifest_checksum=episode_checksum)}
    failures = {
        "wrong-pin": {"pins": [p for p in pins if p != scene_pin] + [wrong]},
        # A content clause naming the RobotRun; the LeRobot content has no Scene.
        "robot-run-as-content": {"selector": {"sources": [
            {"domain": "scene", "source_kind": "content", "source_id": recording["robot_run_id"],
             "producer_fingerprint": nuscenes["build"]["producer_fingerprint"]}]}},
        "lerobot-scenes": {"selector": {"sources": [
            {"domain": "scene", "source_kind": "content", "source_id": pusht["snapshot"]["content_id"],
             "producer_fingerprint": pusht["build"]["producer_fingerprint"]}]}},
        # Bound to a name already bound to another composition.
        "rebind": {"pins": pusht["pins"], "dataset_version": "pins"},
    }  # fmt: skip
    for name, members in failures.items():
        params = {"dataset_id": DATASET, "dataset_version": f"invalid-{name}", **members}
        job = _job(api, "compose_dataset_version", params)
        assert job["status"] == "failed", (name, job)
    # Two revisions of one unit and a pin whose artifact id is not its own are
    # refused before a Job exists.
    second_revision = {**pins[0], "manifest_checksum": episode_checksum,
                       "manifest_artifact_id": manifest_artifact_id_for(unit_id=pins[0]["unit_id"], manifest_checksum=episode_checksum)}
    foreign = {**scene_pin, "manifest_artifact_id": pusht["pins"][0]["manifest_artifact_id"]}
    for name, members in (("duplicate", pins + [second_revision]), ("foreign", [foreign])):
        params = {"dataset_id": DATASET, "dataset_version": f"invalid-{name}", "pins": members}
        response = api.post("/jobs", {"type": "compose_dataset_version", "params": params})
        assert response.status_code == 400, (name, response.text)
    assert _rows() == before


def test_native_labels_resolve_only_into_the_content_scene_of_a_mixed_version(
    api: Api, nuscenes: dict, recording: dict, pins: list[dict], by_pins: dict
) -> None:
    """S6 on the mixed version: the nuScenes Scene's own labels attach to it, never
    to the RobotRun Scene of the same nuScenes scene, whose lidar readings carry
    the same timestamps; importing labels changes no DatasetVersion."""
    imported = _succeeded(api, "import_labels", {
        "snapshot_id": nuscenes["snapshot"]["snapshot_id"], "label_set_id": "nuscenes-scene-0061-mixed",
        "unit_keys": PARTS["nuscenes-scene-0061"]["unit_keys"],
    })  # fmt: skip
    assert (imported["label_count"], imported["content_ids"]) == (4699, [nuscenes["snapshot"]["content_id"]])
    store = _store()

    def view_of(policy: dict) -> tuple[dict, dict]:
        result = _succeeded(api, "build_scene_sample_views", {
            "dataset_id": DATASET, "dataset_version": "pins", "policy": policy,
            "label_sets": [imported["label_set"]],
        })  # fmt: skip
        (ref,) = result["views"]
        artifact = api.get(f"/artifacts/{ref['manifest_artifact_id']}")["artifact"]
        return result, json.loads(asyncio.run(store.read_bytes(artifact["uri"])))

    _, native_view = view_of({"anchor": {"channel": "LIDAR_TOP"}})
    (scene_pin,) = [p for p in nuscenes["pins"] if p["domain"] == "scene"]
    assert native_view["scene"]["scene_id"] == scene_pin["unit_id"]
    (stats,) = native_view["label_stats"]
    assert (stats["covered_observation_count"], stats["attached_label_count"], stats["unattached_label_count"]) == (39, 4699, 0)
    _, robot_view = view_of({"anchor": {"channel": "/lidar/top/points"}})
    (recording_scene,) = [p for p in recording["pins"] if p["domain"] == "scene"]
    assert robot_view["scene"]["scene_id"] == recording_scene["unit_id"]
    (stats,) = robot_view["label_stats"]
    assert (stats["covered_observation_count"], stats["attached_label_count"], stats["unattached_label_count"]) == (0, 0, 0)
    assert {s["labels"][0]["covered"] for s in robot_view["samples"]} == {False}
    recomposed = _succeeded(api, "compose_dataset_version", {"dataset_id": DATASET, "dataset_version": "pins", "pins": pins})
    assert (recomposed["created"], recomposed["composition_checksum"]) == (False, by_pins["composition_checksum"])
    MEASUREMENTS["mixed_native_labels_attached"] = 4699


def test_measurements_are_reported(capsys) -> None:
    with capsys.disabled():
        print("mixed_source_measurements " + json.dumps(MEASUREMENTS, sort_keys=True))
