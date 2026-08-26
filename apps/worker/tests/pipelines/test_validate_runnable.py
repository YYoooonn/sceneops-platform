"""Regression tests for PipelineRunner._validate_runnable.

BLOCKED pipeline runs must be retryable: BLOCKED means a quality gate
stopped the pipeline (e.g. validate_scene), not that the run failed to
execute. The API layer (PipelineService.validate_executable) already allows
redispatching a BLOCKED run; the worker-side check used to disagree and
raise RuntimeError, which this test guards against regressing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sceneops_core.common.time import utc_now
from sceneops_core.pipelines.schemas import (
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineType,
)
from sceneops_worker.pipelines.runner import PipelineRunner

_REJECTED = {
    PipelineRunStatus.SUCCEEDED,
    PipelineRunStatus.RUNNING,
    PipelineRunStatus.CANCELLED,
}
_ALLOWED = {
    PipelineRunStatus.BLOCKED,
    PipelineRunStatus.FAILED,
    PipelineRunStatus.PENDING,
    PipelineRunStatus.QUEUED,
}


def _pipeline_run(status: PipelineRunStatus) -> PipelineRunManifest:
    now = utc_now()
    return PipelineRunManifest(
        pipeline_run_id="run-001",
        type=PipelineType.DATASET_SCENE_INGESTION,
        status=status,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def runner() -> PipelineRunner:
    return PipelineRunner(MagicMock())


@pytest.mark.parametrize("status", sorted(_REJECTED, key=str))
def test_rejects_non_retryable_statuses(runner: PipelineRunner, status) -> None:
    with pytest.raises(RuntimeError):
        runner._validate_runnable(_pipeline_run(status))


@pytest.mark.parametrize("status", sorted(_ALLOWED, key=str))
def test_allows_retryable_statuses(runner: PipelineRunner, status) -> None:
    runner._validate_runnable(_pipeline_run(status))  # should not raise


def test_blocked_is_specifically_retryable(runner: PipelineRunner) -> None:
    runner._validate_runnable(_pipeline_run(PipelineRunStatus.BLOCKED))
