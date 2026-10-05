"""PipelineInputResolver._build_dataset_ref: the dataset input of a task is the
DatasetVersion scope of its pipeline run, nothing derived from the version's
state (no channel requirements, counts or quality cache)."""

from __future__ import annotations

from sceneops_core.pipelines.schemas import (
    DatasetInputRef,
    PipelineRunManifest,
    PipelineRunStatus,
    PipelineType,
)
from sceneops_worker.pipelines.input_resolver import PipelineInputResolver


def _pipeline_run(dataset_id="d1", dataset_version="v1") -> PipelineRunManifest:
    return PipelineRunManifest(
        pipeline_run_id="pr-1",
        type=PipelineType.RECORDING_SCENE_BUILDING,
        status=PipelineRunStatus.RUNNING,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )


def test_the_dataset_ref_is_the_scope_of_the_run() -> None:
    ref = PipelineInputResolver._build_dataset_ref(_pipeline_run("d1", "v1"))

    assert ref == DatasetInputRef(dataset_id="d1", dataset_version="v1")


def test_a_run_without_a_version_has_a_dataset_only_ref() -> None:
    ref = PipelineInputResolver._build_dataset_ref(_pipeline_run("d2", None))

    assert ref == DatasetInputRef(dataset_id="d2", dataset_version=None)


def test_a_run_without_a_dataset_has_no_dataset_ref() -> None:
    assert PipelineInputResolver._build_dataset_ref(_pipeline_run(None, None)) is None


def test_the_dataset_ref_carries_no_version_state() -> None:
    assert set(DatasetInputRef.model_fields) == {"dataset_id", "dataset_version"}
