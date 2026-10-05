"""Per-task DAGs for the SceneOps pipelines: one DAG per pipeline type.

Triggered by AirflowPipelineExecutionBackend.dispatch_pipeline (SceneOps API)
via the Airflow REST API, with `conf={"pipeline_run_id": ...}` and a custom
`dag_run_id` equal to that same pipeline_run_id. The API picks the DAG by
pipeline type: `<pipeline_dag_prefix>_<pipeline type>`.

Each task shells out to the `sceneops-worker` CLI (already built into the
existing worker image) via DockerOperator, so this DAG file has no SceneOps
Python dependencies of its own. See docs/adr/004-airflow-vs-celery.md and
docs/architecture/jobs-and-pipelines.md for why the pipeline-level status
transition is split into explicit `start`/`finalize` tasks rather than living
inside each per-task invocation.

PIPELINE_TASK_IDS mirrors the task ids of
`sceneops_core.pipelines.builtin.BUILTIN_PIPELINE_DEFINITIONS`;
apps/worker/tests/pipelines/test_airflow_dag_mirror.py fails when the two
drift. Tasks run serially in definition order; optional tasks skip
themselves (PipelineTaskRunner), exactly as on the Celery path.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow import DAG
from airflow.providers.docker.operators.docker import DockerOperator
from airflow.utils.trigger_rule import TriggerRule
from docker.types import Mount

WORKER_IMAGE = "sceneops-platform/worker:local"
# SCENEOPS_WORKER_* covers the worker's own prefixed settings, but
# sceneops_db.session's DbSettings reads the bare, unprefixed
# SCENEOPS_DATABASE_URL (no SCENEOPS_WORKER_ prefix). Forward every
# SCENEOPS_*-prefixed var instead of guessing at an exact allowlist.
WORKER_ENV = {k: v for k, v in os.environ.items() if k.startswith("SCENEOPS_")}
PIPELINE_RUN_ID = "{{ dag_run.conf['pipeline_run_id'] }}"
DAG_ID_PREFIX = "sceneops"

# DockerOperator spawns sibling containers via the host docker daemon, so
# compose's own `./data:/data` mount (on worker-pipeline/worker-jobs) does
# not apply here — mount the same host directory explicitly by absolute
# path. HOST_DATA_DIR is set on the scheduler container via compose
# (`${PWD}/data` at `docker compose up` time).
HOST_DATA_DIR = os.environ["HOST_DATA_DIR"]
DATA_MOUNT = Mount(source=HOST_DATA_DIR, target="/data", type="bind")

PIPELINE_TASK_IDS = {
    "recording_scene_building": [
        "build_recording_scenes",
        "register_scenes",
        "validate_scene",
        "profile_scene",
    ],
    "recording_episode_building": [
        "build_recording_episodes",
        "register_episodes",
        "validate_episode",
        "profile_episode",
    ],
    "scene_ml_evaluation": [
        "build_scene_sample_views",
        "mine_scenarios",
        "score_scenario_readiness",
        "predict_detection",
        "evaluate_detection",
    ],
    "episode_learning_data_building": [
        "align_episode",
        "export_learning_data",
    ],
}


def worker_task(task_id: str, *cli_args: str) -> DockerOperator:
    return DockerOperator(
        task_id=task_id,
        image=WORKER_IMAGE,
        command=["sceneops-worker", *cli_args],
        docker_url="unix://var/run/docker.sock",
        network_mode="sceneops-network",
        environment=WORKER_ENV,
        mounts=[DATA_MOUNT],
        auto_remove="success",
        mount_tmp_dir=False,
    )


def build_pipeline_dag(pipeline_type: str, task_ids: list[str]) -> DAG:
    with DAG(
        dag_id=f"{DAG_ID_PREFIX}_{pipeline_type}",
        description=f"Per-task DAG for the {pipeline_type} pipeline",
        schedule=None,
        start_date=datetime(2026, 1, 1),
        catchup=False,
        max_active_runs=10,
    ) as dag:
        start = worker_task(
            "start", "pipelines", "start", "--pipeline-run-id", PIPELINE_RUN_ID
        )

        task_chain = [
            worker_task(
                task_id,
                "run-pipeline-task",
                "--pipeline-run-id",
                PIPELINE_RUN_ID,
                "--task-id",
                task_id,
            )
            for task_id in task_ids
        ]

        finalize = worker_task(
            "finalize", "pipelines", "finalize", "--pipeline-run-id", PIPELINE_RUN_ID
        )
        finalize.trigger_rule = TriggerRule.ALL_DONE

        start >> task_chain[0]
        for upstream, downstream in zip(task_chain, task_chain[1:]):
            upstream >> downstream
        task_chain[-1] >> finalize
    return dag


for _pipeline_type, _task_ids in PIPELINE_TASK_IDS.items():
    globals()[f"{DAG_ID_PREFIX}_{_pipeline_type}"] = build_pipeline_dag(
        _pipeline_type, _task_ids
    )
