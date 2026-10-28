"""nuScenes scene-0061 slice -> canonical Scenes on a disposable runtime
(`make test-infrastructure SUITE=nuscenes-scenes`; ADR-015 §4, §6, §7; M3 S4).

The raw slice is published by the Publisher process and registered by a
REGISTER_SOURCE_SNAPSHOT Job; SNAPSHOT_SCENE_BUILDING then builds, registers,
composes, validates and profiles its Scene on the runtime's Celery workers, and
the Scene is held to the independent raw-source verifier
(tools/reference/nuscenes_scene_verify.py, a separate read-only process). Every
expectation comes from the raw tree through that verifier, from the M3 plan's
measured numbers, from the RobotRun Scene of the same nuScenes scene (the
cross-source comparison), or from the platform's own earlier answers
(idempotency), never from the code under test.

Prerequisites: the nuScenes v1.0-mini tree at data/raw/nuscenes and the locked
reference recording of scene-0061 (`make reference-data-bootstrap`), which the
`baseline` fixture registers. The measurements are printed as one
``nuscenes_scene_measurements`` JSON line.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from infra_support import JOB_TERMINAL, REPO_ROOT, Api
from sceneops_core.artifacts.schemas.enums import ArtifactBackend
from sceneops_core.config import StorageSettings
from sceneops_storage.backends.s3 import S3ArtifactStore

SNAPSHOTS = json.loads(
    (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
)["snapshots"]
SLICE = SNAPSHOTS["nuscenes-scene-0061"]
BUILD_CONFIG = json.loads(
    (REPO_ROOT / "config/reference/nuscenes-scene-0061/scene_build_config.json").read_text()
)
SCENE_0061 = "cc8c0bf57f984915a77078b10eb33198"
# Another scene of the shared tables, whose files the slice does not preserve.
SCENE_0103 = "fcbccedd61424f1b85dcbf8f897f9754"
DATASET = "sceneops-test-nuscenes-scene-0061"
MEASUREMENTS: dict[str, float | int] = {}

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / SLICE["local_root"] / "v1.0-mini").is_dir(),
    reason="data/raw/nuscenes is missing",
)


# ── processes and the control plane ──────────────────────────────────────────


def _publish(revision: str) -> dict:
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
        "--source-dir", str(REPO_ROOT / SLICE["local_root"]), "--format", SLICE["format"],
        "--upstream", SLICE["upstream"], "--upstream-revision", revision,
    ]  # fmt: skip
    for key, value in SLICE["annotations"].items():
        command += ["--annotation", f"{key}={value}"]
    for pattern in SLICE["include"]:
        command += ["--include", pattern]
    completed = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout)


def _job(api: Api, job_type: str, params: dict, *, timeout: float = 900.0) -> dict:
    created = api.post("/jobs", {"type": job_type, "params": params, "force": True})
    assert created.status_code == 201, created.text
    job = created.json()["job"]
    if job["status"] not in JOB_TERMINAL:
        executed = api.post(f"/jobs/{job['jobId']}/execute", {})
        assert executed.status_code == 200, executed.text
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = api.get(f"/jobs/{job['jobId']}")["job"]
        if job["status"] in JOB_TERMINAL:
            return job
        time.sleep(1)
    raise AssertionError(f"job {job['jobId']} did not finish in {timeout}s")


def _succeeded(api: Api, job_type: str, params: dict) -> dict:
    job = _job(api, job_type, params)
    assert job["status"] == "succeeded", job.get("error") or job
    return job["result"]


def _snapshot(api: Api, revision: str) -> dict:
    published = _publish(revision)
    return _succeeded(
        api, "register_source_snapshot", {"descriptor_uri": published["descriptor_uri"]}
    )


def _build_params(snapshot_id: str, unit_keys: list[str] | None = None) -> dict:
    params: dict = {"snapshot_id": snapshot_id, "build_config": BUILD_CONFIG}
    if unit_keys is not None:
        params["unit_keys"] = unit_keys
    return params


def _verifier(snapshot_id: str, *, dataset: str | None = None) -> dict:
    """The independent raw-source verifier, as its own read-only process."""
    command = [
        sys.executable, "-I", str(REPO_ROOT / "tools/reference/nuscenes_scene_verify.py"),
        "verify", "--snapshot-id", snapshot_id,
    ]  # fmt: skip
    if dataset is not None:
        command += ["--dataset", dataset]
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, check=False, cwd=REPO_ROOT)
    MEASUREMENTS["verify_seconds"] = time.monotonic() - started
    report = json.loads(completed.stdout)
    MEASUREMENTS["verify_checks"] = report["check_count"]
    MEASUREMENTS["verify_controls_detected"] = sum(
        1 for c in report["negative_controls"] or [] if c["detected"]
    )
    assert completed.returncode == 0 and report["ok"], (
        report["violations"][:5],
        report["undetected_controls"],
        completed.stderr[-2000:],
    )
    return report


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


def _manifest(api: Api, artifact_id: str) -> dict:
    artifact = api.get(f"/artifacts/{artifact_id}")["artifact"]
    return json.loads(asyncio.run(_store().read_bytes(artifact["uri"])))


def _revisions(api: Api, content_id: str) -> list[dict]:
    units = api.get("/scenes", content_id=content_id, limit=500)["scenes"]
    return sorted(
        (r for u in units for r in api.get(f"/scenes/{u['sceneId']}")["revisions"]),
        key=lambda r: r["sceneId"],
    )


def _task_result(api: Api, task: dict) -> dict:
    return api.get(f"/jobs/{task['jobId']}")["job"]["result"]


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def snapshot_a(api: Api) -> dict:
    started = time.monotonic()
    result = _snapshot(api, SLICE["upstream_revision"])
    MEASUREMENTS["publish_and_register_seconds"] = time.monotonic() - started
    assert (result["file_count"], result["total_bytes"]) == (2989, 519_531_462)
    return result


@pytest.fixture(scope="module")
def run(api: Api, snapshot_a: dict) -> dict:
    """scene-0061 through SNAPSHOT_SCENE_BUILDING, composed into a
    DatasetVersion by the pipeline's compose stage."""
    api.dataset(DATASET)
    started = time.monotonic()
    pipeline = api.run(
        "snapshot_scene_building",
        {
            "build_snapshot_scenes": _build_params(snapshot_a["snapshot_id"], [SCENE_0061]),
            "compose_dataset_version": {"dataset_id": DATASET, "dataset_version": "scene-0061"},
            "profile_scene": {"triggered": True},
        },
    )
    MEASUREMENTS["pipeline_seconds"] = time.monotonic() - started
    assert pipeline["status"] == "succeeded", pipeline
    return {"run": pipeline, "tasks": api.tasks(pipeline["pipelineRunId"])}


# ── S4 acceptance ────────────────────────────────────────────────────────────


def test_scene_0061_is_built_registered_and_composed(api: Api, snapshot_a: dict, run: dict) -> None:
    tasks = run["tasks"]
    build = _task_result(api, tasks["build_snapshot_scenes"])
    assert (build["snapshot_id"], build["content_id"], build["unit_keys"]) == (
        snapshot_a["snapshot_id"], snapshot_a["content_id"], [SCENE_0061],
    )  # fmt: skip
    assert (build["observation_count"], build["keyframe_count"], build["pose_count"]) == (
        2963, 39, 2963,
    )  # fmt: skip
    register = _task_result(api, tasks["register_scenes"])
    assert (register["source_kind"], register["source_id"]) == ("content", snapshot_a["content_id"])
    assert _task_result(api, tasks["validate_scene"])["status"] == "ready"
    profile = _task_result(api, tasks["profile_scene"])
    assert (profile["observation_count"], profile["keyframe_count"]) == (2963, 39)
    compose = _task_result(api, tasks["compose_dataset_version"])
    assert compose["member_count"] == 1
    version = api.version(DATASET, "scene-0061")
    assert version["scene"]["sceneCount"] == 1 and version["episode"] is None
    report = _verifier(snapshot_a["snapshot_id"], dataset=f"{DATASET}/scene-0061")
    scene = report["summary"]["scenes"]["scene-0061"]
    assert scene["by_modality"] == {"camera": 1318, "lidar": 382, "radar": 1263}
    assert (scene["before_first_sample_ns"], scene["after_last_sample_ns"]) == (
        43_107_000, 29_279_000,
    )  # fmt: skip
    assert {c["control"] for c in report["negative_controls"] if c["detected"]} == {
        c["control"] for c in report["negative_controls"]
    }
    # No payload object: every observation references a preserved file.
    (unit,) = api.get("/scenes", content_id=snapshot_a["content_id"])["scenes"]
    assert (unit["sourceKind"], unit["robotRunId"], unit["unitKey"]) == ("content", None, SCENE_0061)


def test_rebuilding_and_reregistering_write_nothing(api: Api, snapshot_a: dict, run: dict) -> None:
    before_objects = _objects()
    before_revisions = _revisions(api, snapshot_a["content_id"])
    started = time.monotonic()
    build = _succeeded(api, "build_snapshot_scenes", _build_params(snapshot_a["snapshot_id"], [SCENE_0061]))
    MEASUREMENTS["rebuild_seconds"] = time.monotonic() - started
    assert build["created_manifest_count"] == 0
    started = time.monotonic()
    register = _succeeded(api, "register_scenes", {"manifest_artifact_ids": build["manifest_artifact_ids"]})
    MEASUREMENTS["reregister_seconds"] = time.monotonic() - started
    assert register["existing_revision_scene_ids"] == register["scene_ids"]
    assert _revisions(api, snapshot_a["content_id"]) == before_revisions
    assert _objects() == before_objects


def test_a_second_snapshot_of_the_same_bytes_reuses_the_revision(api: Api, snapshot_a: dict, run: dict) -> None:
    snapshot_b = _snapshot(api, SLICE["upstream_revision"] + "-b")
    assert snapshot_b["snapshot_id"] != snapshot_a["snapshot_id"]
    assert (snapshot_b["content_id"], snapshot_b["content"]) == (snapshot_a["content_id"], "existing")
    before_objects = _objects()
    build = _succeeded(api, "build_snapshot_scenes", _build_params(snapshot_b["snapshot_id"], [SCENE_0061]))
    assert (build["snapshot_id"], build["created_manifest_count"]) == (snapshot_b["snapshot_id"], 0)
    register = _succeeded(api, "register_scenes", {"manifest_artifact_ids": build["manifest_artifact_ids"]})
    assert register["existing_revision_scene_ids"] == register["scene_ids"]
    assert _objects() == before_objects
    artifact = api.get(f"/artifacts/{build['manifest_artifact_ids'][0]}")["artifact"]
    assert artifact["jobId"] == run["tasks"]["build_snapshot_scenes"]["jobId"]


def test_composition_by_content_selector_equals_composition_by_pins(api: Api, snapshot_a: dict, run: dict) -> None:
    build = _task_result(api, run["tasks"]["build_snapshot_scenes"])
    composed = _succeeded(
        api,
        "compose_dataset_version",
        {
            "dataset_id": DATASET,
            "dataset_version": "scene-0061-selector",
            "selector": {
                "sources": [
                    {
                        "domain": "scene",
                        "source_kind": "content",
                        "source_id": snapshot_a["content_id"],
                        "producer_fingerprint": build["producer_fingerprint"],
                    }
                ]
            },
        },
    )
    assert composed["composition_checksum"] == api.version(DATASET, "scene-0061")["compositionChecksum"]


@pytest.mark.parametrize(
    ("unit_keys", "message"),
    [(None, "scene-0757"), ([SCENE_0103], "scene-0103")],
)
def test_scenes_whose_files_are_not_preserved_are_refused(
    api: Api, snapshot_a: dict, run: dict, unit_keys, message
) -> None:
    before = _objects()
    job = _job(api, "build_snapshot_scenes", _build_params(snapshot_a["snapshot_id"], unit_keys))
    assert job["status"] == "failed" and message in json.dumps(job["error"]), job["error"]
    assert "must be preserved" in json.dumps(job["error"])
    assert _objects() == before


def test_sample_views_and_detection_resolve_preserved_files(api: Api, run: dict) -> None:
    views = _succeeded(
        api,
        "build_scene_sample_views",
        {
            "dataset_id": DATASET,
            "dataset_version": "scene-0061",
            "policy": {
                "anchor": {"channel": "LIDAR_TOP"},
                "members": [{"channel": "CAM_FRONT", "tolerance_ns": 50_000_000}],
            },
        },
    )
    # One sample per LIDAR_TOP reading, unless no CAM_FRONT image lies within
    # the tolerance (a required member drops its anchor).
    assert views["scene_count"] == 1
    assert views["sample_count"] + views["dropped_anchor_count"] == 382
    model = "sceneops-test-nuscenes-mock"
    if api._client.get(f"/models/{model}").status_code == 404:
        assert api.post("/models", {"modelId": model, "name": model, "metadata": {}}).status_code in (200, 201)
    if api._client.get(f"/models/{model}/versions/1").status_code == 404:
        created = api.post(f"/models/{model}/versions", {"version": "1", "backend": "mock", "metadata": {}})
        assert created.status_code in (200, 201), created.text
    predicted = _succeeded(
        api,
        "predict_detection",
        {
            "dataset_id": DATASET,
            "dataset_version": "scene-0061",
            "model_id": model,
            "model_version": "1",
            "sample_views": views["views"],
            "camera_channel": "CAM_FRONT",
            "max_samples": 5,
        },
    )
    assert predicted["sample_count"] == 5


def test_camera_and_lidar_agree_with_the_robot_run_scene(api: Api, baseline_run: dict, run: dict) -> None:
    """Cross-source: the RobotRun Scene of scene-0061 (Recording Import,
    sensor.header_stamp) and the nuScenes Scene (nuscenes.timestamp) are on
    different clock domains; their numbers are compared, not their domains."""
    assert baseline_run["source_unit"] == "scene-0061"
    (recording_revision,) = api.revisions("scenes", baseline_run["robot_run_id"])
    recording = _manifest(api, recording_revision["manifestArtifactId"])
    build = _task_result(api, run["tasks"]["build_snapshot_scenes"])
    native = _manifest(api, build["manifest_artifact_ids"][0])

    def rows(manifest: dict, channel: str, *, checksum: bool) -> list:
        return [
            (o["timestamp_ns"], o["payload"]["checksum" if o["payload"]["kind"] == "artifact" else "sha256"])
            if checksum
            else o["timestamp_ns"]
            for o in manifest["observations"]
            if o["channel"] == channel
        ]

    camera = rows(native, "CAM_FRONT", checksum=True)
    assert len(camera) == 224
    assert rows(recording, "/camera/front/image/compressed", checksum=True) == camera
    lidar = rows(native, "LIDAR_TOP", checksum=False)
    assert len(lidar) == 382
    assert rows(recording, "/lidar/top/points", checksum=False) == lidar
    MEASUREMENTS["cross_source_camera"] = len(camera)
    MEASUREMENTS["cross_source_lidar"] = len(lidar)


def test_measurements_are_reported(capsys) -> None:
    with capsys.disabled():
        print("nuscenes_scene_measurements " + json.dumps(MEASUREMENTS, sort_keys=True))
