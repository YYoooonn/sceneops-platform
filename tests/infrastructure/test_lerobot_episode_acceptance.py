"""LeRobot pusht -> canonical Episodes on a disposable runtime
(`make test-infrastructure SUITE=lerobot-episodes`; ADR-015 §4, §6, §7; M3 S3).

The pinned pusht copy is published by the Publisher process and registered by a
REGISTER_SOURCE_SNAPSHOT Job; SNAPSHOT_EPISODE_BUILDING then builds, registers,
composes, validates and profiles its Episodes on the runtime's Celery workers.
Episode 0 is held to the independent raw-source verifier first
(tools/reference/lerobot_verify.py, a separate read-only process), then the
whole corpus with its DatasetVersion. Every expectation comes from the raw tree
through that verifier, from the M3 plan's measured numbers, or from the
platform's own earlier answers (idempotency), never from the code under test.

Prerequisites: `make reference-source-fetch` (data/raw/lerobot/pusht). The
measurements are printed as one ``lerobot_episode_measurements`` JSON line.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from infra_support import JOB_TERMINAL, REPO_ROOT, Api
from sceneops_core.artifacts.schemas.enums import ArtifactBackend
from sceneops_core.config import StorageSettings
from sceneops_storage.backends.s3 import S3ArtifactStore

SNAPSHOTS = json.loads(
    (REPO_ROOT / "config/reference/source-snapshots.json").read_text()
)["snapshots"]
PUSHT = SNAPSHOTS["lerobot-pusht"]
BUILD_CONFIG = json.loads(
    (REPO_ROOT / "config/reference/lerobot-pusht/episode_build_config.json").read_text()
)
DATASET = "sceneops-test-lerobot-pusht"
MEASUREMENTS: dict[str, float | int] = {}

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / PUSHT["local_root"]).is_dir(),
    reason="data/raw/lerobot/pusht is missing: run `make reference-source-fetch`",
)


# ── processes and the control plane ──────────────────────────────────────────


def _publish(source_dir: Path, upstream: str, revision: str) -> dict:
    root = os.environ["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"]
    env = {
        **os.environ,
        "SCENEOPS_PUBLISHER_ARTIFACT__BACKEND": "minio",
        "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI": root,
        "SCENEOPS_PUBLISHER_ARTIFACT__ENDPOINT_URL": os.environ["MINIO_ENDPOINT_URL"],
        "SCENEOPS_PUBLISHER_ARTIFACT__ACCESS_KEY_ID": os.environ["MINIO_ROOT_USER"],
        "SCENEOPS_PUBLISHER_ARTIFACT__SECRET_ACCESS_KEY": os.environ[
            "MINIO_ROOT_PASSWORD"
        ],
    }
    command = [
        sys.executable, "-m", "sceneops_publisher", "publish-snapshot",
        "--source-dir", str(source_dir), "--format", "lerobot",
        "--upstream", upstream, "--upstream-revision", revision,
        "--annotation", "license=mit",
    ]  # fmt: skip
    for pattern in PUSHT["include"]:
        command += ["--include", pattern]
    completed = subprocess.run(
        command, capture_output=True, text=True, env=env, check=False
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout)


def _job(
    api: Api, job_type: str, params: dict, *, force: bool = True, timeout: float = 900.0
) -> dict:
    created = api.post("/jobs", {"type": job_type, "params": params, "force": force})
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


def _snapshot(api: Api, source_dir: Path, upstream: str, revision: str) -> dict:
    published = _publish(source_dir, upstream, revision)
    return _succeeded(
        api, "register_source_snapshot", {"descriptor_uri": published["descriptor_uri"]}
    )


def _build_params(snapshot_id: str, unit_keys: list[str] | None = None) -> dict:
    params: dict = {"snapshot_id": snapshot_id, "build_config": BUILD_CONFIG}
    if unit_keys is not None:
        params["unit_keys"] = unit_keys
    return params


def _verifier(
    snapshot_id: str, *, episode: int | None = None, dataset: str | None = None
) -> dict:
    """The independent raw-source verifier, as its own read-only process."""
    command = [
        sys.executable,
        "-I",
        str(REPO_ROOT / "tools/reference/lerobot_verify.py"),
    ]
    if episode is not None:
        command += ["--episode", str(episode)]
    command += ["verify", "--snapshot-id", snapshot_id]
    if dataset is not None:
        command += ["--dataset", dataset]
    started = time.monotonic()
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, cwd=REPO_ROOT
    )
    MEASUREMENTS[f"verify_seconds.{'all' if episode is None else episode}"] = (
        time.monotonic() - started
    )
    report = json.loads(completed.stdout)
    scope = "all" if episode is None else str(episode)
    MEASUREMENTS[f"verify_checks.{scope}"] = report["check_count"]
    MEASUREMENTS[f"verify_controls_detected.{scope}"] = sum(
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
    import asyncio

    listed = asyncio.run(
        _store().list_objects(os.environ["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"])
    )
    return {o.uri: o.size_bytes for o in listed}


def _revisions(api: Api, content_id: str) -> list[dict]:
    units = api.get("/episodes", content_id=content_id, limit=500)["episodes"]
    return sorted(
        (r for u in units for r in api.get(f"/episodes/{u['episodeId']}")["revisions"]),
        key=lambda r: r["episodeId"],
    )


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def snapshot_a(api: Api) -> dict:
    started = time.monotonic()
    result = _snapshot(
        api,
        REPO_ROOT / PUSHT["local_root"],
        PUSHT["upstream"],
        PUSHT["upstream_revision"],
    )
    MEASUREMENTS["publish_and_register_seconds"] = time.monotonic() - started
    return result


@pytest.fixture(scope="module")
def full_run(api: Api, snapshot_a: dict) -> dict:
    """The whole corpus through SNAPSHOT_EPISODE_BUILDING, composed into a
    DatasetVersion by the pipeline's compose stage."""
    api.dataset(DATASET)
    started = time.monotonic()
    run = api.run(
        "snapshot_episode_building",
        {
            "build_snapshot_episodes": _build_params(snapshot_a["snapshot_id"]),
            "compose_dataset_version": {
                "dataset_id": DATASET,
                "dataset_version": "pusht-all",
            },
            "profile_episode": {"triggered": True},
        },
    )
    MEASUREMENTS["pipeline_all_seconds"] = time.monotonic() - started
    assert run["status"] == "succeeded", run
    return {"run": run, "tasks": api.tasks(run["pipelineRunId"])}


def _task_result(api: Api, task: dict) -> dict:
    return api.get(f"/jobs/{task['jobId']}")["job"]["result"]


# ── S3 acceptance ────────────────────────────────────────────────────────────


def test_episode_zero_first(api: Api, snapshot_a: dict) -> None:
    started = time.monotonic()
    run = api.run(
        "snapshot_episode_building",
        {"build_snapshot_episodes": _build_params(snapshot_a["snapshot_id"], ["0"])},
    )
    MEASUREMENTS["pipeline_episode_0_seconds"] = time.monotonic() - started
    assert run["status"] == "succeeded", run
    tasks = api.tasks(run["pipelineRunId"])
    build = _task_result(api, tasks["build_snapshot_episodes"])
    assert (build["snapshot_id"], build["content_id"], build["unit_keys"]) == (
        snapshot_a["snapshot_id"], snapshot_a["content_id"], ["0"],
    )  # fmt: skip
    assert _task_result(api, tasks["validate_episode"])["status"] == "ready"
    report = _verifier(snapshot_a["snapshot_id"], episode=0)
    assert {c["control"] for c in report["negative_controls"] if c["detected"]} == {
        c["control"] for c in report["negative_controls"]
    }


def test_the_whole_corpus_and_its_dataset_version(
    api: Api, snapshot_a: dict, full_run: dict
) -> None:
    tasks = full_run["tasks"]
    build = _task_result(api, tasks["build_snapshot_episodes"])
    assert (build["episode_count"], build["observation_count"], build["transition_count"]) == (
        206, 25_650, 3 * 25_650,
    )  # fmt: skip
    # Episode 0 was built before: one manifest is reused, 205 are new.
    assert build["created_manifest_count"] == 205
    register = _task_result(api, tasks["register_episodes"])
    assert (register["source_kind"], register["source_id"]) == (
        "content",
        snapshot_a["content_id"],
    )
    assert len(register["existing_revision_episode_ids"]) == 1
    assert len(register["new_unit_episode_ids"]) == 205
    compose = _task_result(api, tasks["compose_dataset_version"])
    assert compose["member_count"] == 206
    version = api.version(DATASET, "pusht-all")
    assert version["episode"]["episodeCount"] == 206 and version["scene"] is None
    report = _verifier(snapshot_a["snapshot_id"], dataset=f"{DATASET}/pusht-all")
    assert report["summary"]["next.done true"] == 412


def test_rebuilding_writes_no_row_artifact_or_object(
    api: Api, snapshot_a: dict, full_run: dict
) -> None:
    before_objects = _objects()
    before_revisions = _revisions(api, snapshot_a["content_id"])
    started = time.monotonic()
    build = _succeeded(
        api, "build_snapshot_episodes", _build_params(snapshot_a["snapshot_id"])
    )
    MEASUREMENTS["rebuild_seconds"] = time.monotonic() - started
    assert build["created_manifest_count"] == 0
    started = time.monotonic()
    register = _succeeded(
        api,
        "register_episodes",
        {"manifest_artifact_ids": build["manifest_artifact_ids"]},
    )
    MEASUREMENTS["reregister_seconds"] = time.monotonic() - started
    assert len(register["existing_revision_episode_ids"]) == 206
    assert _revisions(api, snapshot_a["content_id"]) == before_revisions
    assert _objects() == before_objects


def test_a_second_snapshot_of_the_same_bytes_reuses_every_revision(
    api: Api, snapshot_a: dict, full_run: dict
) -> None:
    snapshot_b = _snapshot(
        api,
        REPO_ROOT / PUSHT["local_root"],
        PUSHT["upstream"],
        PUSHT["upstream_revision"] + "-b",
    )
    assert snapshot_b["snapshot_id"] != snapshot_a["snapshot_id"]
    assert (snapshot_b["content_id"], snapshot_b["content"]) == (
        snapshot_a["content_id"],
        "existing",
    )
    before_objects = _objects()
    first_build_job = full_run["tasks"]["build_snapshot_episodes"]["jobId"]
    build = _succeeded(
        api, "build_snapshot_episodes", _build_params(snapshot_b["snapshot_id"])
    )
    assert build["snapshot_id"] == snapshot_b["snapshot_id"]
    assert build["created_manifest_count"] == 0
    register = _succeeded(
        api,
        "register_episodes",
        {"manifest_artifact_ids": build["manifest_artifact_ids"]},
    )
    assert len(register["existing_revision_episode_ids"]) == 206
    assert _objects() == before_objects
    # The revisions keep the provenance of the build that first produced them.
    artifact = api.get(f"/artifacts/{build['manifest_artifact_ids'][1]}")["artifact"]
    assert artifact["jobId"] == first_build_job


def test_composition_by_content_selector_equals_composition_by_pins(
    api: Api, snapshot_a: dict, full_run: dict
) -> None:
    build = _task_result(api, full_run["tasks"]["build_snapshot_episodes"])
    composed = _succeeded(
        api,
        "compose_dataset_version",
        {
            "dataset_id": DATASET,
            "dataset_version": "pusht-selector",
            "selector": {
                "sources": [
                    {
                        "domain": "episode",
                        "source_kind": "content",
                        "source_id": snapshot_a["content_id"],
                        "producer_fingerprint": build["producer_fingerprint"],
                    }
                ]
            },
        },
    )
    by_pins = api.version(DATASET, "pusht-all")
    assert composed["composition_checksum"] == by_pins["compositionChecksum"]


def _faulty_copy(tmp_path: Path, name: str, fault) -> Path:
    root = tmp_path / name
    shutil.copytree(REPO_ROOT / PUSHT["local_root"], root)
    fault(root)
    return root


def _off_grid(root: Path) -> None:
    path = root / "data/chunk-000/file-000.parquet"
    table = pq.read_table(path)
    stamps = table.column("timestamp").to_pylist()
    stamps[5] = struct.unpack("<f", struct.pack("<f", stamps[5] + 0.001))[0]
    column = table.column_names.index("timestamp")
    pq.write_table(
        table.set_column(column, "timestamp", pa.array(stamps, pa.float32())), path
    )


def _v2(root: Path) -> None:
    path = root / "meta/info.json"
    info = json.loads(path.read_text())
    info["codebase_version"] = "v2.1"
    path.write_text(json.dumps(info, indent=4))


@pytest.mark.parametrize(
    ("name", "fault", "message"),
    [("off-grid", _off_grid, "is not float32"), ("v2", _v2, "not supported")],
)
def test_contents_the_builder_refuses_are_preserved_but_never_built(
    api: Api, tmp_path: Path, name, fault, message
) -> None:
    snapshot = _snapshot(
        api, _faulty_copy(tmp_path, name, fault), f"test/pusht-{name}", "r1"
    )
    assert snapshot["content"] == "created"
    job = _job(api, "build_snapshot_episodes", _build_params(snapshot["snapshot_id"]))
    assert job["status"] == "failed" and message in json.dumps(job["error"]), job[
        "error"
    ]
    assert api.get("/episodes", content_id=snapshot["content_id"])["episodes"] == []


def test_an_unknown_unit_key_is_refused(
    api: Api, snapshot_a: dict, full_run: dict
) -> None:
    before = _objects()
    job = _job(
        api,
        "build_snapshot_episodes",
        _build_params(snapshot_a["snapshot_id"], ["0", "206"]),
    )
    assert job["status"] == "failed" and "name no episode" in json.dumps(job["error"])
    assert _objects() == before


def test_alignment_refuses_a_pusht_episode(api: Api, full_run: dict) -> None:
    (member, *_) = api.members("episodes", DATASET, "pusht-all")
    job = _job(
        api,
        "align_episode",
        {
            "episodes": [
                {
                    "episode_id": member["episodeId"],
                    "source_artifact_id": member["manifestArtifactId"],
                    "source_manifest_sha256": member["manifestChecksum"].removeprefix(
                        "sha256:"
                    ),
                }
            ],
            "dataset_id": DATASET,
            "dataset_version": "pusht-all",
            "alignment_config": {"target_frequency_hz": 10.0},
        },
    )
    assert job["status"] == "failed"
    assert "transition streams" in json.dumps(job["error"]), job["error"]


def test_an_episode_built_without_transitions_aligns_its_video_frame_references(
    api: Api, snapshot_a: dict
) -> None:
    """The same pinned content built without its transition features (``next.*``):
    alignment accepts the Episode and carries each video-frame PayloadRef whole --
    uri, checksum, size, media type and selector. References are aligned; no
    frame is decoded."""
    from sceneops_core.episodes.alignment.persistence import AlignedEpisodeArtifact
    from sceneops_core.episodes.alignment.semantics import ALIGNMENT_SEMANTICS_VERSION
    from sceneops_core.episodes.schemas.manifests import load_canonical_episode_manifest

    features = [f for f in BUILD_CONFIG["features"] if f["role"] != "transition"]
    api.dataset(DATASET)
    run = api.run(
        "snapshot_episode_building",
        {
            "build_snapshot_episodes": {
                "snapshot_id": snapshot_a["snapshot_id"],
                "build_config": {**BUILD_CONFIG, "features": features},
                "unit_keys": ["0"],
            },
            "compose_dataset_version": {
                "dataset_id": DATASET,
                "dataset_version": "pusht-0-no-transitions",
            },
        },
    )
    assert run["status"] == "succeeded", run
    (member,) = api.members("episodes", DATASET, "pusht-0-no-transitions")
    record = api.get(f"/artifacts/{member['manifestArtifactId']}")["artifact"]
    manifest = load_canonical_episode_manifest(
        asyncio.run(_store().read_bytes(record["uri"]))
    )
    assert manifest.transitions == []
    # Every frame is a selector into one MP4 object: a pin is (uri, metadata) by frame time.
    pins = {
        (o.topic, o.timestamp_ns): (o.payload.uri, o.payload.model_dump(mode="json", exclude={"uri"}, exclude_none=True))
        for o in manifest.observations
        if o.payload is not None
    }  # fmt: skip
    assert pins and all(m["selector"]["kind"] == "video_frame" for _, m in pins.values())

    job = _job(
        api,
        "align_episode",
        {
            "episodes": [
                {
                    "episode_id": member["episodeId"],
                    "source_artifact_id": member["manifestArtifactId"],
                    "source_manifest_sha256": member["manifestChecksum"].removeprefix("sha256:"),
                }
            ],
            "dataset_id": DATASET,
            "dataset_version": "pusht-0-no-transitions",
            # pusht's 10 fps grid; half a frame period finds each frame's reference.
            "alignment_config": {"target_frequency_hz": 10.0, "tolerance_us": 50_000, "max_gap_us": 200_000},
        },
    )  # fmt: skip
    assert job["status"] == "succeeded", job.get("error")
    (ref,) = job["result"]["aligned"]
    aligned = AlignedEpisodeArtifact.model_validate(
        json.loads(asyncio.run(_store().read_bytes(ref["aligned_artifact_uri"])))
    ).aligned_episode
    assert aligned.alignment_semantics_version == ALIGNMENT_SEMANTICS_VERSION == "v3"
    references = [
        s
        for step in aligned.steps
        for s in step.observations.values()
        if s.value is not None and s.value.reference_uri is not None
    ]
    assert references
    for signal in references:
        value = signal.value
        frame = (value.reference_channel, signal.source_timestamp_us * 1000)
        assert pins[frame] == (value.reference_uri, value.reference_metadata)
    assert len({json.dumps(s.value.reference_metadata, sort_keys=True) for s in references}) == len(references)
    MEASUREMENTS["aligned_video_references"] = len(references)
    MEASUREMENTS["aligned_steps"] = aligned.step_count


def test_measurements_are_reported(capsys) -> None:
    with capsys.disabled():
        print(
            "lerobot_episode_measurements " + json.dumps(MEASUREMENTS, sort_keys=True)
        )
