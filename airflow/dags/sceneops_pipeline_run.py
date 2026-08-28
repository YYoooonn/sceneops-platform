"""Per-task DAG PoC for the SceneOps `dataset_scene_ingestion` pipeline.

Triggered by AirflowPipelineExecutionBackend.dispatch_pipeline (SceneOps API)
via the Airflow REST API, with `conf={"pipeline_run_id": ...}` and a custom
`dag_run_id` equal to that same pipeline_run_id.

Each task shells out to the `sceneops-worker` CLI (already built into the
existing worker image) via DockerOperator, so this DAG has no SceneOps
Python dependencies of its own. See docs/adr/004-airflow-vs-celery.md and
docs/pipeline-lifecycle.md for why the pipeline-level status transition is
split into explicit `start`/`finalize` tasks rather than living inside each
per-task invocation.

Limitation: the task chain below is hardcoded to `dataset_scene_ingestion`.
This DAG does not generalize to other SceneOps pipeline types yet.
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
# SCENEOPS_DATABASE_URL (no SCENEOPS_WORKER_ prefix) — found by actually
# running this and watching `sceneops-worker pipelines start` fail with
# "1 validation error for DbSettings / SCENEOPS_DATABASE_URL / Field
# required". Forward every SCENEOPS_*-prefixed var instead of guessing at
# an exact allowlist.
WORKER_ENV = {k: v for k, v in os.environ.items() if k.startswith("SCENEOPS_")}
PIPELINE_RUN_ID = "{{ dag_run.conf['pipeline_run_id'] }}"

# DockerOperator spawns sibling containers via the host docker daemon, so
# compose's own `./data:/data` mount (on worker-pipeline/worker-jobs) does
# not apply here — mount the same host directory explicitly by absolute
# path. HOST_DATA_DIR is set on the scheduler container via compose
# (`${PWD}/data` at `docker compose up` time).
HOST_DATA_DIR = os.environ["HOST_DATA_DIR"]
DATA_MOUNT = Mount(source=HOST_DATA_DIR, target="/data", type="bind")

PIPELINE_TASK_IDS = [
    "ingest_scenes",
    "register_scene",
    "validate_scene",
    "profile_scene",
    "build_scene_index",
    "build_dataset_manifest",
]


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


with DAG(
    dag_id="sceneops_pipeline_run",
    description="Per-task PoC DAG for the dataset_scene_ingestion pipeline",
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
        for task_id in PIPELINE_TASK_IDS
    ]

    finalize = worker_task(
        "finalize", "pipelines", "finalize", "--pipeline-run-id", PIPELINE_RUN_ID
    )
    finalize.trigger_rule = TriggerRule.ALL_DONE

    start >> task_chain[0]
    for upstream, downstream in zip(task_chain, task_chain[1:]):
        upstream >> downstream
    task_chain[-1] >> finalize
