"""Detection's readiness gate: validation results of exactly the Scene
revisions the dataset manifest pins (ADR-007 §13.4, §18.5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.datasets.schemas import DatasetManifest, DatasetSceneIndexEntry
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenes import SceneReadiness, SceneValidationRunRecord
from sceneops_worker.scenes.readiness import (
    pinned_revision_readiness,
    require_no_blocked_scenes,
)

PIN = ("art-1", "sha256:" + "1" * 64)


def _manifest() -> DatasetManifest:
    return DatasetManifest(
        dataset_id="ds",
        dataset_version="v1",
        scenes=[
            DatasetSceneIndexEntry(
                scene_id="scene-a",
                manifest_artifact_id=PIN[0],
                manifest_checksum=PIN[1],
                manifest_uri="s3://b/m.json",
            )
        ],
    )


def _context(runs) -> MagicMock:
    context = MagicMock()

    async def list_runs(**filters):
        assert filters["manifest_artifact_id"] == PIN[0]
        return [
            r for r in runs if r.manifest_artifact_id == filters["manifest_artifact_id"]
        ]

    context.runs.scene_runs.list = AsyncMock(side_effect=list_runs)
    return context


def _run(run_id, *, revision=PIN, block=False):
    return SceneValidationRunRecord(
        run_id=run_id,
        scene_id="scene-a",
        manifest_artifact_id=revision[0],
        manifest_checksum=revision[1],
        status=RunStatus.SUCCEEDED,
        validation_status="failed" if block else "ready",
        should_block_pipeline=block,
    )


async def test_unvalidated_revision_is_unknown_and_allowed():
    context = _context([])
    assert await pinned_revision_readiness(context, _manifest()) == {
        "scene-a": SceneReadiness.UNKNOWN
    }
    await require_no_blocked_scenes(context, _manifest())


async def test_blocked_pinned_revision_refuses_the_workflow():
    context = _context([_run("val-1", block=True)])
    with pytest.raises(ValueError, match="blocked downstream use"):
        await require_no_blocked_scenes(context, _manifest())


async def test_block_on_another_revision_does_not_count():
    # Same artifact id, different checksum: not the pinned revision.
    context = _context(
        [_run("val-1", revision=(PIN[0], "sha256:" + "2" * 64), block=True)]
    )
    await require_no_blocked_scenes(context, _manifest())
