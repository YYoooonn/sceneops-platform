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


# ── recording pipelines: Build -> Register -> Compose -> assess ─────────────


def _revision_pin(domain: str) -> dict:
    from sceneops_core.provenance import manifest_artifact_id_for, unit_id_for

    unit_id = unit_id_for(
        domain=domain, source_kind="recording", source_id="run-1", unit_key="p/0"
    )
    checksum = "sha256:" + "e" * 64
    return {
        "domain": domain,
        "unit_id": unit_id,
        "manifest_checksum": checksum,
        "manifest_artifact_id": manifest_artifact_id_for(
            unit_id=unit_id, manifest_checksum=checksum
        ),
    }


_RECORDING_PIPELINES = [
    ("recording_scene_building", "scene", "build_recording_scenes"),
    ("recording_episode_building", "episode", "build_recording_episodes"),
]


def _recording_inputs(task, refs: dict, params: dict) -> PipelineTaskInputs:
    # A recording pipeline run has no DatasetVersion scope.
    return _inputs(task, refs).model_copy(update={"dataset": None, "params": params})


@pytest.mark.parametrize(
    ("pipeline_type", "domain", "build_task"), _RECORDING_PIPELINES
)
def test_a_recording_pipeline_hands_each_stage_what_it_needs_and_no_dataset_version(
    pipeline_type: str, domain: str, build_task: str
) -> None:
    definition = next(
        d for d in BUILTIN_PIPELINE_DEFINITIONS if d.type == pipeline_type
    )
    by_id = {t.pipeline_task_id: t for t in definition.tasks}
    pin = _revision_pin(domain)
    ref_values = {
        "manifest_artifact_ids": ["manifest-1"],
        "robot_run_id": "run-1",
        "revisions": [pin],
        f"{domain}_ids": [pin["unit_id"]],
    }
    caller = {
        build_task: {"robot_run_id": "run-1", "build_config": {}},
        "compose_dataset_version": {"dataset_id": "d", "dataset_version": "v1"},
    }

    for task in definition.tasks:
        upstream = {
            o.name: ref_values[o.name]
            for dep in task.depends_on_pipeline_task_ids
            for o in by_id[dep].outputs
            if o.kind == PipelineTaskOutputKind.REF and o.name in ref_values
        }
        params = _REGISTRY.get(task.job_type).build_job_params(
            _recording_inputs(task, upstream, caller.get(task.pipeline_task_id, {}))
        )
        if task.pipeline_task_id == build_task:
            assert params == caller[build_task]
            continue
        parsed = parse_job_params(task.job_type, params)
        if task.pipeline_task_id == "compose_dataset_version":
            assert (parsed.dataset_id, parsed.dataset_version) == ("d", "v1")
            assert [p.model_dump() for p in parsed.pins] == [pin]
            assert parsed.selector is None
        elif task.job_type.value.startswith("register_"):
            assert parsed.manifest_artifact_ids == ["manifest-1"]
        else:
            assert [p.model_dump() for p in parsed.revisions] == [pin]
        assert "dataset_version" not in params or (
            task.pipeline_task_id == "compose_dataset_version"
        )


@pytest.mark.parametrize("named", [{"pins": []}, {"selector": {"sources": []}}])
def test_a_pipeline_composition_takes_its_members_only_from_the_register_stage(
    named: dict,
) -> None:
    definition = next(
        d for d in BUILTIN_PIPELINE_DEFINITIONS if d.type == "recording_scene_building"
    )
    compose = next(
        t for t in definition.tasks if t.pipeline_task_id == "compose_dataset_version"
    )
    handler = _REGISTRY.get(compose.job_type)
    target = {"dataset_id": "d", "dataset_version": "v1"}
    with pytest.raises(ValueError, match="not accepted"):
        handler.build_job_params(
            _recording_inputs(
                compose, {"revisions": [_revision_pin("scene")]}, {**target, **named}
            )
        )
    with pytest.raises(ValueError, match="no revisions to compose"):
        handler.build_job_params(_recording_inputs(compose, {}, target))


def test_only_the_composition_job_of_a_recording_run_names_a_dataset_version() -> None:
    from sceneops_core.common.time import utc_now
    from sceneops_core.pipelines.schemas import (
        PipelineRunManifest,
        PipelineRunStatus,
        PipelineTaskRunManifest,
        PipelineTaskRunStatus,
        PipelineType,
    )
    from sceneops_worker.pipelines.planning import PipelineJobPlanner

    definition = next(
        d for d in BUILTIN_PIPELINE_DEFINITIONS if d.type == "recording_scene_building"
    )
    target = {"dataset_id": "d", "dataset_version": "v1"}
    run = PipelineRunManifest(
        pipeline_run_id="pipe-1",
        type=PipelineType.RECORDING_SCENE_BUILDING,
        status=PipelineRunStatus.RUNNING,
        params={"compose_dataset_version": target},
    )
    refs = {"revisions": [_revision_pin("scene")], "manifest_artifact_ids": ["m"]}
    labels = {}
    for task in definition.tasks:
        if task.pipeline_task_id == "build_recording_scenes":
            continue
        task_run = PipelineTaskRunManifest(
            pipeline_task_run_id=f"ptr-{task.pipeline_task_id}",
            pipeline_run_id="pipe-1",
            pipeline_task_id=task.pipeline_task_id,
            pipeline_task_name=task.name,
            task_order=task.order,
            status=PipelineTaskRunStatus.PENDING,
            job_type=task.job_type,
            params=run.params.get(task.pipeline_task_id, {}),
            created_at=utc_now(),
        )
        job = PipelineJobPlanner().build_job_for_task(
            pipeline_run=run,
            task=task_run,
            inputs=_recording_inputs(task, refs, {}),
        )
        labels[task.pipeline_task_id] = (job.dataset_id, job.dataset_version)

    assert labels == {
        "register_scenes": (None, None),
        "compose_dataset_version": ("d", "v1"),
        "validate_scene": (None, None),
        "profile_scene": (None, None),
    }
