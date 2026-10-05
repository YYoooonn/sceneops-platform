"""Scene readiness is derived only from validation runs of the exact
manifest revision being assessed (ADR-007 §13.4, I-16)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes import (
    SceneReadiness,
    SceneValidationRunRecord,
    derive_scene_readiness,
    latest_validation_for_revision,
)

REV_1 = ("art-rev-1", "sha256:" + "1" * 64)
REV_2 = ("art-rev-2", "sha256:" + "2" * 64)


def _run(
    run_id, revision, *, status="ready", block=False, run_status=RunStatus.SUCCEEDED
):
    return SceneValidationRunRecord(
        run_id=run_id,
        scene_id="scene-a",
        manifest_artifact_id=revision[0],
        manifest_checksum=revision[1],
        status=run_status,
        validation_status=status,
        should_block_pipeline=block,
    )


def _lookup(runs, revision):
    return latest_validation_for_revision(
        runs,
        scene_id="scene-a",
        manifest_artifact_id=revision[0],
        manifest_checksum=revision[1],
    )


def test_run_for_another_revision_never_counts():
    runs = [_run("val-2", REV_1, status="ready")]
    assert _lookup(runs, REV_2) is None
    assert derive_scene_readiness(_lookup(runs, REV_2)) == SceneReadiness.UNKNOWN


def test_newest_matching_run_wins_and_older_revisions_are_skipped():
    runs = [
        _run("val-3", REV_2, status="warning"),
        _run("val-2", REV_1, status="failed", block=True),
        _run("val-1", REV_1, status="ready"),
    ]
    assert _lookup(runs, REV_1).run_id == "val-2"
    assert derive_scene_readiness(_lookup(runs, REV_1)) == SceneReadiness.BLOCKED
    assert derive_scene_readiness(_lookup(runs, REV_2)) == SceneReadiness.WARNING


def test_unfinished_runs_are_ignored():
    runs = [
        _run("val-2", REV_1, run_status=RunStatus.RUNNING),
        _run("val-1", REV_1, status="ready"),
    ]
    assert _lookup(runs, REV_1).run_id == "val-1"
    assert derive_scene_readiness(_lookup(runs, REV_1)) == SceneReadiness.READY


@pytest.mark.parametrize(
    ("status", "block", "expected"),
    [
        ("ready", False, SceneReadiness.READY),
        ("warning", False, SceneReadiness.WARNING),
        ("failed", False, SceneReadiness.BLOCKED),
        ("error", False, SceneReadiness.BLOCKED),
        ("ready", True, SceneReadiness.BLOCKED),
        (None, False, SceneReadiness.UNKNOWN),
    ],
)
def test_readiness_mapping(status, block, expected):
    assert (
        derive_scene_readiness(_run("val-1", REV_1, status=status, block=block))
        == expected
    )


def test_per_scene_run_records_must_pin_a_revision():
    with pytest.raises(ValidationError, match="must pin"):
        SceneValidationRunRecord(
            run_id="val-1", scene_id="scene-a", manifest_artifact_id="a"
        )
    with pytest.raises(ValidationError, match="cannot pin"):
        SceneValidationRunRecord(
            run_id="val-1", manifest_artifact_id="a", manifest_checksum="c"
        )
    # Job-level aggregate records carry no scene and no pin.
    assert SceneValidationRunRecord(run_id="val-1").scene_id is None
