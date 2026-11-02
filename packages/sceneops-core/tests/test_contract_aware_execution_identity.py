"""VALIDATE_ALIGNED_EPISODE, PROFILE_ALIGNED_EPISODE, EXPORT_LEARNING_DATA and
EXPORT_ANALYTICS_SNAPSHOT are keyed on the contracts their results depend on, and no
other JobType moved.

The frozen keys were computed by the code before those contracts entered the keys:
what a Job that succeeded back then still has in its execution_key column. Reuse
against real rows is proven in
apps/worker/tests/episodes/test_learning_schema_upgrade_integration.py and
apps/worker/tests/datasets/test_scene_analytics_upgrade_integration.py.
"""

from __future__ import annotations

import pytest

from sceneops_core.episodes.alignment import (
    ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION,
    ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
    ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
)
from sceneops_core.episodes.learning_export import LEARNING_DATA_SCHEMA_VERSION
from sceneops_core.executions import compute_execution_key, params_for_execution_key
from sceneops_core.executions import key as key_module
from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.builtin import BUILTIN_PIPELINE_DEFINITIONS
from sceneops_core.scenes.analytics import SCENE_ANALYTICS_SCHEMA_VERSION

A = "a" * 64

CASES = {
    JobType.ALIGN_EPISODE: {
        "episodes": [
            {
                "episode_id": "ep-1",
                "source_artifact_id": "src",
                "source_manifest_sha256": A,
            }
        ],
        "alignment_config": {"target_frequency_hz": 1.0},
        "source_clock": None,
    },
    JobType.VALIDATE_ALIGNED_EPISODE: {
        "episode_id": "ep-1",
        "aligned_artifact_id": "art",
        "aligned_artifact_checksum": A,
    },
    JobType.PROFILE_ALIGNED_EPISODE: {
        "episode_id": "ep-1",
        "aligned_artifact_id": "art",
        "aligned_artifact_checksum": A,
    },
    JobType.EXPORT_LEARNING_DATA: {
        "inputs": [
            {
                "episode_id": "ep-1",
                "aligned_artifact_id": "art",
                "aligned_artifact_checksum": A,
            }
        ],
        "tables": None,
        "base_export_id": None,
    },
    JobType.CURATE_EPISODES: {
        "learning_data_export_manifest_artifact_id": "m",
        "learning_data_export_manifest_checksum": A,
        "policy": {"required_observation_channels": ["b", "a"]},
    },
    JobType.VALIDATE_EPISODE: {"episode_id": "ep-1"},
    JobType.EXPORT_ANALYTICS_SNAPSHOT: {"tables": ["scenes"]},
    JobType.EXPORT_ROBOT_ANALYTICS_SNAPSHOT: {"robot_run_id": "run-1"},
    JobType.BUILD_SNAPSHOT_EPISODES: {"snapshot_id": "s", "build_config": {}},
}

KEYS_BEFORE_THE_CONTRACTS_ENTERED = {
    JobType.VALIDATE_ALIGNED_EPISODE: "validate_aligned_episode:3e802bb3c3c96a20b4b29ec8",
    JobType.PROFILE_ALIGNED_EPISODE: "profile_aligned_episode:3956ff6aeb0d93fdf5b18afe",
    JobType.EXPORT_LEARNING_DATA: "export_learning_data:68b8cb2b59fa111c9e55aede",
    JobType.EXPORT_ANALYTICS_SNAPSHOT: "export_analytics_snapshot:7a22c52ac893847fa4751373",
}

UNCHANGED_KEYS = {
    JobType.ALIGN_EPISODE: "align_episode:462f59d5daee999048546fe8",
    JobType.CURATE_EPISODES: "curate_episodes:acedcf635c741449030094c1",
    JobType.VALIDATE_EPISODE: "validate_episode:74588db3473c412a1f29bd6d",
    JobType.BUILD_SNAPSHOT_EPISODES: "build_snapshot_episodes:bd53dd92c3269fc2ad19483f",
}


def _key(job_type: JobType, params: dict | None = None) -> str:
    return compute_execution_key(
        kind="job",
        type=job_type.value,
        dataset_id="d",
        dataset_version="v",
        params=params_for_execution_key(job_type, params or CASES[job_type]),
    )


@pytest.mark.parametrize("job_type", KEYS_BEFORE_THE_CONTRACTS_ENTERED)
def test_a_job_keyed_before_the_contract_entered_has_another_key(job_type) -> None:
    assert _key(job_type) != KEYS_BEFORE_THE_CONTRACTS_ENTERED[job_type]


@pytest.mark.parametrize("job_type", UNCHANGED_KEYS)
def test_every_other_job_type_keeps_its_key(job_type) -> None:
    assert _key(job_type) == UNCHANGED_KEYS[job_type]


def test_validate_and_profile_state_what_their_result_depends_on() -> None:
    validate = params_for_execution_key(
        JobType.VALIDATE_ALIGNED_EPISODE, CASES[JobType.VALIDATE_ALIGNED_EPISODE]
    )
    assert validate == {
        "episode_id": "ep-1",
        "aligned_artifact_checksum": A,
        "aligned_artifact_schema_version": ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION,
        "validation_semantics_version": ALIGNED_EPISODE_VALIDATION_SEMANTICS_VERSION,
    }
    profile = params_for_execution_key(
        JobType.PROFILE_ALIGNED_EPISODE, CASES[JobType.PROFILE_ALIGNED_EPISODE]
    )
    assert profile == {
        "episode_id": "ep-1",
        "aligned_artifact_checksum": A,
        "aligned_artifact_schema_version": ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION,
        "profile_semantics_version": ALIGNED_EPISODE_PROFILE_SEMANTICS_VERSION,
    }


@pytest.mark.parametrize(
    "job_type",
    [JobType.VALIDATE_ALIGNED_EPISODE, JobType.PROFILE_ALIGNED_EPISODE],
)
def test_validate_and_profile_keys_follow_the_real_schema_constant(
    job_type, monkeypatch
) -> None:
    current = _key(job_type)
    monkeypatch.setattr(key_module, "ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION", "v9")
    assert _key(job_type) != current


@pytest.mark.parametrize(
    "job_type",
    [JobType.VALIDATE_ALIGNED_EPISODE, JobType.PROFILE_ALIGNED_EPISODE],
)
def test_validate_and_profile_keys_still_ignore_lineage(job_type) -> None:
    other = {**CASES[job_type], "aligned_artifact_id": "another-record-of-same-bytes"}
    assert _key(job_type, other) == _key(job_type)
    assert _key(
        job_type, {**CASES[job_type], "aligned_artifact_checksum": "b" * 64}
    ) != (_key(job_type))


def test_export_key_follows_the_learning_schema_and_not_the_aligned_one(
    monkeypatch,
) -> None:
    """A finished export stays complete and current as long as its own schema
    does; its inputs are pinned by content and it does not read them again."""
    current = _key(JobType.EXPORT_LEARNING_DATA)
    params = params_for_execution_key(
        JobType.EXPORT_LEARNING_DATA, CASES[JobType.EXPORT_LEARNING_DATA]
    )
    assert params["learning_data_schema_version"] == LEARNING_DATA_SCHEMA_VERSION
    assert "aligned_artifact_schema_version" not in params

    monkeypatch.setattr(key_module, "ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION", "v9")
    assert _key(JobType.EXPORT_LEARNING_DATA) == current
    monkeypatch.setattr(key_module, "LEARNING_DATA_SCHEMA_VERSION", "v9")
    assert _key(JobType.EXPORT_LEARNING_DATA) != current


def test_analytics_export_key_follows_the_scene_analytics_schema_alone(
    monkeypatch,
) -> None:
    current = _key(JobType.EXPORT_ANALYTICS_SNAPSHOT)
    params = params_for_execution_key(
        JobType.EXPORT_ANALYTICS_SNAPSHOT, CASES[JobType.EXPORT_ANALYTICS_SNAPSHOT]
    )
    assert params == {
        "tables": ["scenes"],
        "scene_analytics_schema_version": SCENE_ANALYTICS_SCHEMA_VERSION,
    }

    monkeypatch.setattr(key_module, "LEARNING_DATA_SCHEMA_VERSION", "v9")
    monkeypatch.setattr(key_module, "ALIGNED_EPISODE_ARTIFACT_SCHEMA_VERSION", "v9")
    assert _key(JobType.EXPORT_ANALYTICS_SNAPSHOT) == current
    monkeypatch.setattr(key_module, "SCENE_ANALYTICS_SCHEMA_VERSION", "v9")
    assert _key(JobType.EXPORT_ANALYTICS_SNAPSHOT) != current


def test_export_key_is_still_order_and_lineage_independent() -> None:
    one = {
        "episode_id": "a",
        "aligned_artifact_id": "x",
        "aligned_artifact_checksum": "1",
    }
    two = {
        "episode_id": "b",
        "aligned_artifact_id": "y",
        "aligned_artifact_checksum": "2",
    }
    forward = {"inputs": [one, two], "tables": ["learning_steps", "learning_episodes"]}
    backward = {
        "inputs": [two, {**one, "aligned_artifact_id": "other"}],
        "tables": ["learning_episodes", "learning_steps"],
    }
    assert _key(JobType.EXPORT_LEARNING_DATA, forward) == _key(
        JobType.EXPORT_LEARNING_DATA, backward
    )


@pytest.mark.parametrize("job_type", CASES)
def test_repeating_a_request_converges_and_leaves_its_params_alone(job_type) -> None:
    before = repr(CASES[job_type])
    assert _key(job_type) == _key(job_type)
    assert repr(CASES[job_type]) == before


def test_the_analytics_export_runs_as_a_job_and_in_no_pipeline() -> None:
    """So no reused PipelineRun can answer for a snapshot: its Job's key alone
    decides. A pipeline that gains this task inherits PipelineRun reuse, which does
    not see output contracts (ADR-019), and has to say how it upgrades."""
    task_types = {
        task.job_type
        for definition in BUILTIN_PIPELINE_DEFINITIONS
        for task in definition.tasks
    }
    assert JobType.EXPORT_LEARNING_DATA in task_types
    assert JobType.EXPORT_ANALYTICS_SNAPSHOT not in task_types
