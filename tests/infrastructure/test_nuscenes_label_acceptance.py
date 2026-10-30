"""Native nuScenes labels of the scene-0061 slice on a disposable runtime
(`make test-infrastructure SUITE=nuscenes-labels`; ADR-015 §9; M3 S6).

The raw slice is published by the Publisher process and registered by a
REGISTER_SOURCE_SNAPSHOT Job; SNAPSHOT_SCENE_BUILDING builds, registers and
composes its Scene; IMPORT_LABELS {snapshot_id} imports the slice's own
annotations on the runtime's Celery workers, and BUILD_SCENE_SAMPLE_VIEWS
builds a CAM_FRONT- and a LIDAR_TOP-anchored view pinning them. The label set,
the Scene and both views are held to the independent raw-source verifier
(tools/reference/nuscenes_label_verify.py, a separate read-only process).
Every expectation comes from the raw tree through that verifier, from the M3
plan's measured numbers, from the locked reference labels the RobotRun label
path renders (the cross-path comparison), or from the platform's own earlier
answers (idempotency), never from the code under test.

Prerequisites: the nuScenes v1.0-mini tree at data/raw/nuscenes and the locked
reference labels (`make reference-data-bootstrap`). The measurements are
printed as one ``nuscenes_label_measurements`` JSON line.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from infra_support import REPO_ROOT, Api
from test_nuscenes_scene_acceptance import (
    SCENE_0061,
    SLICE,
    _build_params,
    _job,
    _manifest,
    _objects,
    _snapshot,
    _succeeded,
)

DATASET = "sceneops-test-nuscenes-labels"
LABEL_SET_ID = "nuscenes-scene-0061"
LABEL_COUNT, ANCHOR_COUNT = 4699, 39
REFERENCE_LABELS = sorted(
    (REPO_ROOT / "data/reference/nuscenes-mini-v1/labels").glob("scene-0061-*.labels.json")
)
MEASUREMENTS: dict[str, float | int] = {}

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / SLICE["local_root"] / "v1.0-mini").is_dir(),
    reason="data/raw/nuscenes is missing",
)


def _import_params(snapshot_id: str, **extra) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "label_set_id": LABEL_SET_ID,
        "unit_keys": [SCENE_0061],
        **extra,
    }


def _view_policy(anchor: str, member: str) -> dict:
    return {
        "anchor": {"channel": anchor},
        "members": [{"channel": member, "tolerance_ns": 50_000_000, "required": False}],
    }


def _verifier(snapshot_id: str, label_set_artifact: str, views: list[str]) -> dict:
    command = [
        sys.executable, "-I", str(REPO_ROOT / "tools/reference/nuscenes_label_verify.py"),
        "verify", "--snapshot-id", snapshot_id, "--label-set-artifact", label_set_artifact,
    ]  # fmt: skip
    for view in views:
        command += ["--view-artifact", view]
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


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def snapshot_a(api: Api) -> dict:
    result = _snapshot(api, SLICE["upstream_revision"])
    assert (result["file_count"], result["total_bytes"]) == (2989, 519_531_462)
    return result


@pytest.fixture(scope="module")
def scene(api: Api, snapshot_a: dict) -> dict:
    api.dataset(DATASET)
    pipeline = api.run(
        "snapshot_scene_building",
        {
            "build_snapshot_scenes": _build_params(snapshot_a["snapshot_id"], [SCENE_0061]),
            "compose_dataset_version": {"dataset_id": DATASET, "dataset_version": "scene-0061"},
        },
    )
    assert pipeline["status"] == "succeeded", pipeline
    return api.version(DATASET, "scene-0061")


@pytest.fixture(scope="module")
def imported(api: Api, snapshot_a: dict, scene: dict) -> dict:
    started = time.monotonic()
    job = _job(api, "import_labels", _import_params(snapshot_a["snapshot_id"]))
    MEASUREMENTS["import_seconds"] = time.monotonic() - started
    assert job["status"] == "succeeded", job.get("error") or job
    return job


@pytest.fixture(scope="module")
def views(api: Api, imported: dict) -> dict[str, dict]:
    out = {}
    for anchor, member in (("CAM_FRONT", "LIDAR_TOP"), ("LIDAR_TOP", "CAM_FRONT")):
        out[anchor] = _succeeded(
            api,
            "build_scene_sample_views",
            {
                "dataset_id": DATASET,
                "dataset_version": "scene-0061",
                "policy": _view_policy(anchor, member),
                "label_sets": [imported["result"]["label_set"]],
            },
        )
    return out


# ── S6 acceptance ────────────────────────────────────────────────────────────


def test_import_produces_one_label_set_of_the_slice(api: Api, snapshot_a: dict, imported: dict) -> None:
    result = imported["result"]
    assert (result["label_count"], result["covered_anchor_count"]) == (LABEL_COUNT, ANCHOR_COUNT)
    assert (result["snapshot_id"], result["content_id"]) == (
        snapshot_a["snapshot_id"], snapshot_a["content_id"],
    )  # fmt: skip
    assert (result["content_ids"], result["robot_run_ids"]) == ([snapshot_a["content_id"]], [])
    assert (result["provenance_kind"], result["unit_keys"]) == ("external", [SCENE_0061])
    artifact = api.get(f"/artifacts/{result['label_set']['manifest_artifact_id']}")["artifact"]
    assert (artifact["kind"], artifact["ownerType"], artifact["ownerId"]) == (
        "label_set_manifest", "label_set", LABEL_SET_ID,
    )  # fmt: skip
    assert artifact["checksum"] == result["label_set"]["manifest_checksum"]


def test_views_resolve_the_same_labels_and_pass_the_raw_source_verifier(
    api: Api, snapshot_a: dict, imported: dict, views: dict
) -> None:
    for anchor, result in views.items():
        assert result["scene_count"] == 1, anchor
    report = _verifier(
        snapshot_a["snapshot_id"],
        imported["result"]["label_set"]["manifest_artifact_id"],
        [v["views"][0]["manifest_artifact_id"] for v in views.values()],
    )
    assert report["summary"]["label_count"] == LABEL_COUNT
    assert {c["control"] for c in report["negative_controls"] if c["detected"]} == {
        c["control"] for c in report["negative_controls"]
    }
    for anchor, result in views.items():
        view = _manifest(api, result["views"][0]["manifest_artifact_id"])
        (stats,) = view["label_stats"]
        assert stats["attached_label_count"] + stats["unattached_label_count"] == LABEL_COUNT
        MEASUREMENTS[f"{anchor}_attached"] = stats["attached_label_count"]
        MEASUREMENTS[f"{anchor}_samples"] = len(view["samples"])


def test_reimport_and_views_change_nothing(api: Api, snapshot_a: dict, scene: dict, imported: dict, views: dict) -> None:
    label_set = imported["result"]["label_set"]
    before_objects = _objects()
    before_bytes = _manifest(api, label_set["manifest_artifact_id"])
    again = _succeeded(api, "import_labels", _import_params(snapshot_a["snapshot_id"]))
    assert (again["label_set"], again["created"]) == (label_set, False)
    assert _objects() == before_objects
    # The label set is not a DatasetVersion member, and building views never
    # rewrites it.
    assert api.version(DATASET, "scene-0061")["compositionChecksum"] == scene["compositionChecksum"]
    assert _manifest(api, label_set["manifest_artifact_id"]) == before_bytes


def test_a_second_snapshot_of_the_same_bytes_yields_the_same_revision(
    api: Api, snapshot_a: dict, imported: dict
) -> None:
    snapshot_b = _snapshot(api, SLICE["upstream_revision"] + "-labels-b")
    assert (snapshot_b["content_id"], snapshot_b["content"]) == (snapshot_a["content_id"], "existing")
    before = _objects()
    through_b = _succeeded(api, "import_labels", _import_params(snapshot_b["snapshot_id"]))
    assert (through_b["label_set"], through_b["created"]) == (imported["result"]["label_set"], False)
    assert through_b["snapshot_id"] == snapshot_b["snapshot_id"]
    assert _objects() == before
    artifact = api.get(f"/artifacts/{through_b['label_set']['manifest_artifact_id']}")["artifact"]
    assert artifact["jobId"] == imported["jobId"]


def test_refused_imports_write_nothing(api: Api, snapshot_a: dict, imported: dict) -> None:
    before = _objects()
    # Without unit_keys the slice names scenes whose key frame files it does not hold.
    job = _job(api, "import_labels", {"snapshot_id": snapshot_a["snapshot_id"], "label_set_id": "gt-all"})
    assert job["status"] == "failed" and "is not a file of" in json.dumps(job["error"]), job["error"]
    created = api.post(
        "/jobs",
        {"type": "import_labels", "params": {"snapshot_id": snapshot_a["snapshot_id"], "label_set_id": "x", "dataset_version": "v"}},
    )
    assert created.status_code in (400, 422), created.text
    assert _objects() == before


def test_native_labels_equal_the_locked_reference_labels_of_the_robot_run_path(api: Api, imported: dict) -> None:
    """The RobotRun label path renders the locked reference labels of scene-0061;
    the native import must carry the same annotations, boxes and key-frame times."""
    assert REFERENCE_LABELS, "no locked reference labels; run `make reference-data-bootstrap`"
    reference = json.loads(REFERENCE_LABELS[-1].read_text())
    native = _manifest(api, imported["result"]["label_set"]["manifest_artifact_id"])
    expected = {
        label["label_id"]: (
            sample["anchor_timestamp_ns"], sample["sample_token"], label["instance_id"],
            label["category"], sorted(label["attributes"]),
            [float(v) for v in label["center_m"]], [float(v) for v in label["size_wlh_m"]],
            [float(v) for v in label["rotation_wxyz"]],
        )
        for sample in reference["samples"]
        for label in sample["labels"]
    }  # fmt: skip
    observed = {
        label["label_id"]: (
            label["anchor"]["timestamp_ns"], label["anchor"]["group_ref"], label["instance_id"],
            label["category"], label["attributes"], label["box"]["center_m"],
            label["box"]["size_wlh_m"], label["box"]["rotation_wxyz"],
        )
        for label in native["labels"]
    }  # fmt: skip
    assert len(expected) == LABEL_COUNT
    assert observed == expected
    # Frame names follow each Scene's own frames (the recording's map frame,
    # the content Scene's global frame); the values are the same source values.
    assert {label["box"]["frame_id"] for label in native["labels"]} == {"global"}
    MEASUREMENTS["reference_labels_equal"] = len(observed)


def test_measurements_are_reported(capsys) -> None:
    with capsys.disabled():
        print("nuscenes_label_measurements " + json.dumps(MEASUREMENTS, sort_keys=True))
