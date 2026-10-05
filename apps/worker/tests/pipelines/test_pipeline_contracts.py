"""Pipeline contracts below E2E: every declared task has a handler, and the
REFs one stage declares are exactly what the next stage's handler consumes.

The params a caller supplies per task are the only free input; everything a
stage takes from an upstream stage must arrive through a declared REF output,
or the pipeline cannot run.
"""

from __future__ import annotations

import pytest

from sceneops_core.episodes.alignment import TemporalAlignmentConfig
from sceneops_core.jobs.schemas import parse_job_params
from sceneops_core.pipelines.builtin import BUILTIN_PIPELINE_DEFINITIONS
from sceneops_core.pipelines.schemas import (
    DatasetInputRef,
    ModelInputRef,
    PipelineInputRef,
    PipelineTaskInputs,
    PipelineTaskOutputKind,
)
from sceneops_worker.jobs.registry import create_default_job_handler_registry

_REGISTRY = create_default_job_handler_registry()

_SAMPLE_VIEW = {
    "scene_id": "scene-1",
    "manifest_artifact_id": "sampleview-scene-1-abc",
    "manifest_checksum": "sha256:" + "a" * 64,
}
_LABEL_SET = {
    "label_set_id": "labels-1",
    "manifest_artifact_id": "labelset-1-abc",
    "manifest_checksum": "sha256:" + "b" * 64,
}
_EXPORT_INPUT = {
    "episode_id": "episode-1",
    "aligned_artifact_id": "aligned-episode-1-abc",
    "aligned_artifact_checksum": "sha256:" + "c" * 64,
}

# Plausible upstream REF values, by the REF name a stage declares.
_REF_VALUES = {
    "views": [_SAMPLE_VIEW],
    "scenario_set_id": "scenarioset-1",
    "inference_run_id": "inference-run-1",
    "prediction_manifest_checksum": "sha256:" + "d" * 64,
    "export_inputs": [_EXPORT_INPUT],
    "manifest_artifact_ids": ["manifest-1"],
    "scene_ids": ["scene-1"],
    "episode_ids": ["episode-1"],
}

_ALIGNMENT = TemporalAlignmentConfig(target_frequency_hz=5.0).model_dump(
    mode="json", exclude_none=True
)

# The params a caller must supply for each task of each pipeline.
_CALLER_PARAMS = {
    "build_scene_sample_views": {
        "policy": {
            "anchor": {"channel": "/cam", "stride": 1},
            "members": [],
        },
        "label_sets": [_LABEL_SET],
    },
    "mine_scenarios": {"label_set_id": "labels-1"},
    "predict_detection": {"camera_channel": "/cam"},
    "evaluate_detection": {"label_set": _LABEL_SET},
    "align_episode": {
        "episodes": [{"episode_id": "episode-1"}],
        "alignment_config": _ALIGNMENT,
    },
}


def _inputs(task, upstream_refs: dict) -> PipelineTaskInputs:
    return PipelineTaskInputs(
        pipeline=PipelineInputRef(
            pipeline_run_id="pipe-1",
            pipeline_type="x",
            task_id=task.pipeline_task_id,
            pipeline_task_id=task.pipeline_task_id,
            pipeline_task_run_id="ptask-1",
        ),
        dataset=DatasetInputRef(dataset_id="d", dataset_version="v1"),
        model=ModelInputRef(model_id="m", model_version="1"),
        refs=upstream_refs,
        params={**task.default_params, **_CALLER_PARAMS.get(task.pipeline_task_id, {})},
    )


def test_every_task_has_a_registered_handler() -> None:
    for definition in BUILTIN_PIPELINE_DEFINITIONS:
        for task in definition.tasks:
            handler = _REGISTRY.get(task.job_type)
            assert handler.job_type == task.job_type


def test_every_dependency_hands_over_at_least_one_declared_ref() -> None:
    for definition in BUILTIN_PIPELINE_DEFINITIONS:
        by_id = {t.pipeline_task_id: t for t in definition.tasks}
        for task in definition.tasks:
            for dep in task.depends_on_pipeline_task_ids:
                refs = [
                    o
                    for o in by_id[dep].outputs
                    if o.kind == PipelineTaskOutputKind.REF
                ]
                assert refs, f"{definition.type}: {dep} -> {task.pipeline_task_id}"


@pytest.mark.parametrize(
    "pipeline_type,task_id",
    [
        ("scene_ml_evaluation", "mine_scenarios"),
        ("scene_ml_evaluation", "predict_detection"),
        ("scene_ml_evaluation", "evaluate_detection"),
        ("episode_learning_data_building", "export_learning_data"),
    ],
)
def test_a_stage_takes_its_pinned_input_from_the_upstream_ref(
    pipeline_type: str, task_id: str
) -> None:
    definition = next(
        d for d in BUILTIN_PIPELINE_DEFINITIONS if d.type == pipeline_type
    )
    task = next(t for t in definition.tasks if t.pipeline_task_id == task_id)

    # The refs this stage's upstream tasks declare, with plausible values.
    by_id = {t.pipeline_task_id: t for t in definition.tasks}
    upstream: dict = {}
    for dep in task.depends_on_pipeline_task_ids:
        for output in by_id[dep].outputs:
            if output.kind == PipelineTaskOutputKind.REF:
                upstream[output.name] = _REF_VALUES.get(output.name, f"{output.name}-x")

    handler = _REGISTRY.get(task.job_type)
    params = handler.build_job_params(_inputs(task, upstream))
    parsed = parse_job_params(task.job_type, params)

    if task_id == "mine_scenarios":
        assert [v.manifest_artifact_id for v in parsed.sample_views] == [
            _SAMPLE_VIEW["manifest_artifact_id"]
        ]
    elif task_id == "predict_detection":
        assert parsed.scenario_set_id == "scenarioset-1"
        assert parsed.sample_views == []
    elif task_id == "evaluate_detection":
        assert parsed.inference_run_id == "inference-run-1"
        assert parsed.prediction_manifest_checksum == "sha256:" + "d" * 64
    else:
        assert [i.model_dump(exclude={"metadata"}) for i in parsed.inputs] == [
            _EXPORT_INPUT
        ]


def test_explicit_params_win_over_upstream_refs() -> None:
    """A caller that pins sample views itself is not overridden by a ref."""
    definition = next(
        d for d in BUILTIN_PIPELINE_DEFINITIONS if d.type == "scene_ml_evaluation"
    )
    task = next(
        t for t in definition.tasks if t.pipeline_task_id == "predict_detection"
    )
    inputs = _inputs(task, {"scenario_set_id": "scenarioset-from-ref"})
    inputs = inputs.model_copy(
        update={"params": {**inputs.params, "sample_views": [_SAMPLE_VIEW]}}
    )
    params = _REGISTRY.get(task.job_type).build_job_params(inputs)
    assert "scenario_set_id" not in params
    assert parse_job_params(task.job_type, params).sample_views


def test_a_stage_without_its_ref_does_not_invent_an_input() -> None:
    definition = next(
        d
        for d in BUILTIN_PIPELINE_DEFINITIONS
        if d.type == "episode_learning_data_building"
    )
    task = next(
        t for t in definition.tasks if t.pipeline_task_id == "export_learning_data"
    )
    params = _REGISTRY.get(task.job_type).build_job_params(_inputs(task, {}))
    with pytest.raises(ValueError, match="at least one input"):
        parse_job_params(task.job_type, params)
