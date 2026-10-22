"""Source preservation acceptance on real MinIO and PostgreSQL
(`make test-infrastructure SUITE=sources`; ADR-015 §5).

The reference snapshots of config/reference/source-snapshots.json are published by
the Publisher process (``python -m sceneops_publisher publish-snapshot``) into the
disposable bucket and registered by ``REGISTER_SOURCE_SNAPSHOT`` Jobs that the
disposable runtime's API dispatches to its Celery job worker (the worker image).
Every expectation is derived independently of the code under test: the pusht files
and checksums from its raw-source lock, the scene-0061 slice by walking the raw
nuScenes tree with plain string rules and hashing with hashlib.

Prerequisites: `make reference-source-fetch` (data/raw/lerobot/pusht) and the
nuScenes v1.0-mini tree at data/raw/nuscenes; the suite fails without them. The
measured durations are printed as one ``source_preservation_measurements`` JSON
line at the end.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
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
PUSHT_LOCK = json.loads(
    (REPO_ROOT / "config/reference/lerobot-pusht/source.lock.json").read_text()
)
SLICE_LOG = "n015-2018-07-24-11-22-45+0800__"
MEASUREMENTS: dict[str, float | int] = {}


# ── independent expectations ─────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _slice_files(root: Path) -> dict[str, tuple[int, str]]:
    """The scene-0061 slice by plain rules: the licence, the tables, the maps,
    the CAN extracts of scene-0061, and the samples and sweeps of its log."""
    selected: dict[str, tuple[int, str]] = {}
    for directory, _, names in os.walk(root):
        for name in names:
            relative = (Path(directory) / name).relative_to(root).as_posix()
            parts = relative.split("/")
            keep = (
                relative == "LICENSE"
                or (parts[0] in ("v1.0-mini", "maps") and len(parts) == 2)
                or (parts[0] == "can_bus" and name.startswith("scene-0061_"))
                or (
                    parts[0] in ("samples", "sweeps")
                    and len(parts) == 3
                    and name.startswith(SLICE_LOG)
                )
            )
            if keep:
                path = root / relative
                selected[relative] = (path.stat().st_size, _sha256(path))
    return selected


# ── processes and the control plane ──────────────────────────────────────────


def _publisher_env() -> dict[str, str]:
    root = os.environ["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"]
    return {
        **os.environ,
        "SCENEOPS_PUBLISHER_ARTIFACT__BACKEND": "minio",
        "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI": root,
        "SCENEOPS_PUBLISHER_ARTIFACT__ENDPOINT_URL": os.environ["MINIO_ENDPOINT_URL"],
        "SCENEOPS_PUBLISHER_ARTIFACT__ACCESS_KEY_ID": os.environ["MINIO_ROOT_USER"],
        "SCENEOPS_PUBLISHER_ARTIFACT__SECRET_ACCESS_KEY": os.environ[
            "MINIO_ROOT_PASSWORD"
        ],
    }


def _publish(
    name: str, *, source_dir: Path | None = None, **overrides
) -> subprocess.CompletedProcess:
    entry = {**SNAPSHOTS[name], **overrides}
    command = [
        sys.executable,
        "-m",
        "sceneops_publisher",
        "publish-snapshot",
        "--source-dir",
        str(source_dir or REPO_ROOT / entry["local_root"]),
        "--format",
        entry["format"],
        "--upstream",
        entry["upstream"],
        "--upstream-revision",
        entry["upstream_revision"],
    ]
    for pattern in entry["include"]:
        command += ["--include", pattern]
    for key, value in entry["annotations"].items():
        command += ["--annotation", f"{key}={value}"]
    return subprocess.run(
        command, capture_output=True, text=True, env=_publisher_env(), check=False
    )


def _published(name: str, **overrides) -> dict:
    started = time.monotonic()
    completed = _publish(name, **overrides)
    assert completed.returncode == 0, completed.stderr[-3000:]
    MEASUREMENTS.setdefault(f"publish_seconds.{name}", time.monotonic() - started)
    return json.loads(completed.stdout)


def _register(
    api: Api, descriptor_uri: str, *, force: bool = False, timeout: float = 900.0
) -> dict:
    """REGISTER_SOURCE_SNAPSHOT through the Jobs API, executed by the runtime's
    job worker; returns the finished Job. An identical request returns the Job
    that already succeeded (execution-key deduplication); ``force`` runs a new
    one."""
    created = api.post(
        "/jobs",
        {
            "type": "register_source_snapshot",
            "params": {"descriptor_uri": descriptor_uri},
            "force": force,
        },
    )
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


def _registered(
    api: Api, descriptor_uri: str, name: str, *, force: bool = False
) -> dict:
    started = time.monotonic()
    job = _register(api, descriptor_uri, force=force)
    assert job["status"] == "succeeded", job.get("error") or job
    MEASUREMENTS.setdefault(f"register_seconds.{name}", time.monotonic() - started)
    return job


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


def _sources_root() -> str:
    return os.environ["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"].rstrip("/") + "/sources"


def _objects() -> dict[str, int]:
    listed = asyncio.run(_store().list_objects(_sources_root()))
    return {obj.uri: obj.size_bytes for obj in listed}


def _snapshots_of(api: Api, content_id: str) -> list[dict]:
    return api.get("/source-snapshots", content_id=content_id)["sourceSnapshots"]


def _inventory(uri: str) -> dict[str, tuple[int, str]]:
    document = json.loads(asyncio.run(_store().read_bytes(uri)))
    return {f["path"]: (f["size_bytes"], f["sha256"]) for f in document["files"]}


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pusht() -> dict:
    root = REPO_ROOT / SNAPSHOTS["lerobot-pusht"]["local_root"]
    assert root.is_dir(), f"{root} is missing: run `make reference-source-fetch`"
    return _published("lerobot-pusht")


@pytest.fixture(scope="module")
def scene_slice() -> dict:
    root = REPO_ROOT / SNAPSHOTS["nuscenes-scene-0061"]["local_root"]
    assert (root / "v1.0-mini").is_dir(), f"{root} holds no nuScenes v1.0-mini tree"
    return _published("nuscenes-scene-0061")


# ── S1 acceptance ────────────────────────────────────────────────────────────


def test_pusht_preserves_and_registers_exactly_the_locked_files(api, pusht) -> None:
    locked = {f["path"]: (f["size_bytes"], f["sha256"]) for f in PUSHT_LOCK["files"]}
    assert (pusht["file_count"], pusht["total_bytes"]) == (7, 7_684_382)
    assert _inventory(pusht["inventory_uri"]) == locked

    result = _registered(api, pusht["descriptor_uri"], "lerobot-pusht")["result"]

    assert (result["snapshot_id"], result["content_id"]) == (
        pusht["snapshot_id"],
        pusht["content_id"],
    )
    assert (result["content"], result["snapshot"]) == ("created", "created")
    snapshot = api.get(f"/source-snapshots/{pusht['snapshot_id']}")["sourceSnapshot"]
    assert (snapshot["format"], snapshot["upstream"], snapshot["upstreamRevision"]) == (
        "lerobot",
        "lerobot/pusht",
        SNAPSHOTS["lerobot-pusht"]["upstream_revision"],
    )
    content = api.get(f"/source-contents/{pusht['content_id']}")["sourceContent"]
    assert (content["fileCount"], content["totalBytes"]) == (7, 7_684_382)


def test_scene_0061_slice_preserves_every_selected_file_on_minio(
    api, scene_slice
) -> None:
    expected = _slice_files(REPO_ROOT / SNAPSHOTS["nuscenes-scene-0061"]["local_root"])
    assert (len(expected), sum(size for size, _ in expected.values())) == (
        2_989,
        519_531_462,
    )
    assert (scene_slice["file_count"], scene_slice["total_bytes"]) == (
        2_989,
        519_531_462,
    )
    inventory = _inventory(scene_slice["inventory_uri"])
    assert inventory == expected
    assert sum(p.startswith("can_bus/") for p in inventory) == 8
    assert sum(p.startswith("maps/") for p in inventory) == 4
    assert "LICENSE" in inventory
    assert sum("+" in p for p in inventory) == 2_963

    # Every key as written, read back from MinIO and hashed here.
    files_prefix = (
        scene_slice["inventory_uri"].removesuffix("inventory.json") + "files/"
    )
    stored = {
        uri.removeprefix(files_prefix): size
        for uri, size in _objects().items()
        if uri.startswith(files_prefix)
    }
    assert stored == {path: size for path, (size, _) in expected.items()}
    store = _store()

    async def _read_all() -> dict[str, str]:
        semaphore = asyncio.Semaphore(16)

        async def _one(path: str) -> tuple[str, str]:
            async with semaphore:
                data = await store.read_bytes(files_prefix + path)
            return path, "sha256:" + hashlib.sha256(data).hexdigest()

        return dict(await asyncio.gather(*(_one(p) for p in expected)))

    started = time.monotonic()
    read_back = asyncio.run(_read_all())
    MEASUREMENTS["read_back_seconds.nuscenes-scene-0061"] = time.monotonic() - started
    assert read_back == {path: sha for path, (_, sha) in expected.items()}

    result = _registered(api, scene_slice["descriptor_uri"], "nuscenes-scene-0061")[
        "result"
    ]
    assert (result["content"], result["snapshot"]) == ("created", "created")
    assert (result["file_count"], result["total_bytes"]) == (2_989, 519_531_462)


def test_a_second_pusht_declaration_of_identical_bytes_writes_no_file(
    api, pusht
) -> None:
    before = _objects()
    second = _published("lerobot-pusht", upstream_revision="redeclared-7628202a")

    assert second["content_id"] == pusht["content_id"]
    assert second["snapshot_id"] != pusht["snapshot_id"]
    assert (second["files_written"], second["files_reused"]) == (0, 7)
    assert not second["inventory_written"] and second["descriptor_written"]
    assert set(_objects()) - set(before) == {second["descriptor_uri"]}

    result = _registered(api, second["descriptor_uri"], "lerobot-pusht-second")[
        "result"
    ]
    assert (result["content"], result["snapshot"]) == ("existing", "created")
    assert {s["snapshotId"] for s in _snapshots_of(api, pusht["content_id"])} == {
        pusht["snapshot_id"],
        second["snapshot_id"],
    }


def test_inconsistent_declarations_fail_and_write_nothing(api, pusht, tmp_path) -> None:
    before_objects = _objects()
    before_snapshots = _snapshots_of(api, pusht["content_id"])

    # One declaration with other bytes, and with other annotations: refused by
    # publication before anything is written.
    altered = tmp_path / "pusht"
    shutil.copytree(REPO_ROOT / SNAPSHOTS["lerobot-pusht"]["local_root"], altered)
    (altered / "README.md").write_bytes((altered / "README.md").read_bytes() + b"\n")
    other_bytes = _publish("lerobot-pusht", source_dir=altered)
    assert other_bytes.returncode == 1
    assert "one declared revision, two byte sets" in other_bytes.stderr
    other_annotations = _publish("lerobot-pusht", annotations={"license": "apache-2.0"})
    assert other_annotations.returncode == 1
    assert "annotations" in other_annotations.stderr
    # A pattern that matches nothing, or uses a reserved character.
    for include in (["README.md", "nothing/**"], ["README.md", "data/[a]"]):
        completed = _publish("lerobot-pusht", include=include)
        assert completed.returncode == 1, completed.stdout
    assert _objects() == before_objects

    # The same bytes under another format publish (content files are
    # format-free) but cannot register.
    other_format = _published("lerobot-pusht", format="not-lerobot")
    assert other_format["content_id"] == pusht["content_id"]
    assert other_format["files_written"] == 0
    job = _register(api, other_format["descriptor_uri"])
    assert job["status"] == "failed"
    assert "ContentFormatConflictError" in json.dumps(job)
    assert _snapshots_of(api, pusht["content_id"]) == before_snapshots
    missing = api._client.get(f"/source-snapshots/{other_format['snapshot_id']}")
    assert missing.status_code == 404


def test_rerunning_publication_and_registration_writes_nothing(
    api, pusht, scene_slice
) -> None:
    objects = _objects()
    snapshots = {
        name: _snapshots_of(api, published["content_id"])
        for name, published in (("pusht", pusht), ("slice", scene_slice))
    }

    for name, published in (
        ("lerobot-pusht", pusht),
        ("nuscenes-scene-0061", scene_slice),
    ):
        started = time.monotonic()
        again = _published(name)
        MEASUREMENTS[f"republish_seconds.{name}"] = time.monotonic() - started
        assert again["files_written"] == 0
        assert not again["inventory_written"] and not again["descriptor_written"]
        assert again["descriptor_checksum"] == published["descriptor_checksum"]
        # The identical request is the Job that already succeeded: not run again.
        first_job = _register(api, again["descriptor_uri"])
        assert first_job["result"]["snapshot"] == "created"
        # A new Job for the same descriptor converges without writing.
        started = time.monotonic()
        rerun = _registered(api, again["descriptor_uri"], f"{name}-again", force=True)
        MEASUREMENTS[f"reregister_seconds.{name}"] = time.monotonic() - started
        assert rerun["jobId"] != first_job["jobId"]
        result = rerun["result"]
        assert (result["content"], result["snapshot"]) == ("existing", "existing")

    assert _objects() == objects
    assert {
        name: _snapshots_of(api, published["content_id"])
        for name, published in (("pusht", pusht), ("slice", scene_slice))
    } == snapshots


def test_measurements_are_reported(capsys) -> None:
    objects = _objects()
    MEASUREMENTS["bucket_object_count"] = len(objects)
    MEASUREMENTS["bucket_object_bytes"] = sum(objects.values())
    with capsys.disabled():
        print(
            "source_preservation_measurements "
            + json.dumps({k: round(v, 3) for k, v in sorted(MEASUREMENTS.items())}),
            flush=True,
        )
