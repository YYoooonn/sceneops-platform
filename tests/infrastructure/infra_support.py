"""Helpers for the infrastructure acceptance tests (client, params builders).

These run against the live local stack (`make local-up`): the FastAPI control
plane, the Celery workers, PostgreSQL, MinIO and (for the Airflow module) the
Airflow orchestrator. They exercise the contracts of the four pipelines that
no single user journey proves: dedup, force, retry, replacement, blocked
resumption, failure recovery, concurrent registration and execution through
each orchestrator.

The pipelines need a registered RobotRun. The tests consume the golden reference
contract's Recording Import RobotRuns (`scripts/canonical/canonical_bootstrap.sh`,
create-or-verify; the smoke-1 selection, scene-0061, unless REFERENCE_SCOPE says
otherwise), take one of them (`baseline_run`) and build into DatasetVersions the tests own
(class REFERENCE_DERIVED, docs/development/test-matrix.md): no RobotRun is created and the
reference DatasetVersion is never mutated.

Test-owned identity is fixed. `sceneops-test-infra-pipelines` (Celery / default
orchestrator) and `sceneops-test-infra-airflow` (Airflow) each hold one DatasetVersion per
test, named after the test, so a repeated run reuses them instead of adding new ones. A test
therefore starts from whatever state the previous run left and states the transitions it
proves from there (explicit replacement, never deletion). Execution records (PipelineRuns,
Jobs) and the job-keyed validation / profile reports of a re-executed pipeline are history
the platform appends; a runtime reset (`make local-reset`) drops everything.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_PREFIX = os.environ.get("API_PREFIX", "/api/v1")
ENV_FILE = os.environ.get("ENV_FILE", ".env.local")
TERMINAL = {"succeeded", "failed", "blocked", "cancelled"}
JOB_TERMINAL = {"succeeded", "failed", "cancelled", "skipped"}
# The runtime input area IMPORT_LABELS reads, as the host and as the worker see it.
LABELS_DIR = REPO_ROOT / "data" / "inputs" / "labels"
WORKER_LABELS_DIR = "/data/inputs/labels"


def _config(name: str) -> dict:
    return json.loads((REPO_ROOT / "config" / "baselines" / name).read_text())


INFRA_PIPELINES_DATASET = "sceneops-test-infra-pipelines"
INFRA_AIRFLOW_DATASET = "sceneops-test-infra-airflow"

SCENE_BUILD_CONFIG = _config("scene_build_config.json")
EPISODE_BUILD_CONFIG = _config("episode_build_config.json")


class Api:
    """A thin client over the control plane."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get(self, path: str, **params) -> dict:
        response = self._client.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: dict, *, expect: int | None = None) -> httpx.Response:
        response = self._client.post(path, json=body)
        if expect is not None:
            assert response.status_code == expect, response.text
        return response

    def dataset_version(self, dataset_id: str, version: str) -> tuple[str, str]:
        """The test-owned DatasetVersion ``dataset_id`` / ``version``: created when it does
        not exist, reused when it does (a repeated run never adds an identity)."""
        self.post("/datasets", {"dataset_id": dataset_id, "name": "Infrastructure tests"})
        if self._client.get(f"/datasets/{dataset_id}/versions/{version}").status_code == 404:
            self.post(f"/datasets/{dataset_id}/versions", {"version": version}, expect=201)
        return dataset_id, version

    def create_run(
        self, pipeline_type: str, dataset: tuple[str, str], params: dict, *, force: bool = False
    ) -> dict:
        response = self.post(
            "/pipelines/runs",
            {
                "type": pipeline_type,
                "dataset_id": dataset[0],
                "dataset_version": dataset[1],
                "params": params,
                "force": force,
            },
        )
        assert response.status_code in (200, 201), response.text
        return response.json()["pipelineRun"]

    def dispatch(self, run_id: str) -> httpx.Response:
        return self.post(f"/pipelines/runs/{run_id}/execute", {})

    def wait(self, run_id: str, *, timeout: float = 900.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.get(f"/pipelines/runs/{run_id}")["pipelineRun"]
            if run["status"] in TERMINAL:
                return run
            time.sleep(3)
        raise AssertionError(f"pipeline run {run_id} did not finish in {timeout}s")

    def run(
        self, pipeline_type: str, dataset: tuple[str, str], params: dict, *, force: bool = True
    ) -> dict:
        created = self.create_run(pipeline_type, dataset, params, force=force)
        if created["status"] == "succeeded":
            # An identical request returns the run that already holds this result; a
            # succeeded run is not dispatched again (a failed or blocked one is).
            return created
        assert self.dispatch(created["pipelineRunId"]).status_code in (200, 202)
        return self.wait(created["pipelineRunId"])

    def run_job(
        self, job_type: str, dataset: tuple[str, str], params: dict, *, timeout: float = 300.0
    ) -> dict:
        """Create, execute and wait for one atomic Job; returns the terminal job."""
        created = self.post(
            "/jobs",
            {
                "type": job_type,
                "dataset_id": dataset[0],
                "dataset_version": dataset[1],
                "params": params,
                "force": True,
            },
        )
        assert created.status_code in (200, 201), created.text
        job_id = created.json()["job"]["jobId"]
        assert self.post(f"/jobs/{job_id}/execute", {}).status_code in (200, 202)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(f"/jobs/{job_id}")["job"]
            if job["status"] in JOB_TERMINAL:
                return job
            time.sleep(2)
        raise AssertionError(f"job {job_id} did not finish in {timeout}s")

    def tasks(self, run_id: str) -> dict[str, dict]:
        return {t["pipelineTaskId"]: t for t in self.get(f"/pipelines/runs/{run_id}/tasks")["tasks"]}

    def scenes(self, dataset: tuple[str, str]) -> list[dict]:
        return self.get("/scenes", dataset_id=dataset[0], dataset_version=dataset[1], limit=500)["scenes"]

    def episodes(self, dataset: tuple[str, str]) -> list[dict]:
        return self.get("/episodes", dataset_id=dataset[0], dataset_version=dataset[1], limit=500)[
            "episodes"
        ]


@contextlib.contextmanager
def reference_label_document(
    corpus: str, fixture_id: str, robot_run_id: str, label_set_id: str
) -> Iterator[str]:
    """The fixture's locked reference labels rendered for ``robot_run_id`` into the
    runtime input area, as the URI the worker reads (``import_labels.document_uri``).

    The same container ``make e2e-scene-ml`` uses (no source dataset). Needs the
    acquisition image and the prepared reference corpus; a missing prerequisite is an
    assertion failure, never a skip. The file is removed on exit."""
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"{label_set_id}.labels.json"
    result = subprocess.run(
        [
            "docker", "compose", "--env-file", ENV_FILE, "--profile", "acquisition",
            "run", "--rm", "-T", "--no-deps", "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp", "reference-labels", "reference", "render-labels",
            "--corpus", f"/config/reference/{corpus}", "--cache-root", "/reference",
            "--fixture", fixture_id, "--robot-run-id", robot_run_id,
            "--label-set-id", label_set_id, "--output", f"/inputs/labels/{file_name}",
        ],  # fmt: skip
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, (
        f"no verified reference labels for {fixture_id}; run `make acquisition-image` and "
        f"`make reference-data-bootstrap`:\n{result.stdout[-2000:]}{result.stderr[-2000:]}"
    )
    try:
        yield f"{WORKER_LABELS_DIR}/{file_name}"
    finally:
        (LABELS_DIR / file_name).unlink(missing_ok=True)


def scene_params(run_id: str, *, config: dict | None = None, replace: bool = False, **extra) -> dict:
    return {
        "build_recording_scenes": {"robot_run_id": run_id, "build_config": config or SCENE_BUILD_CONFIG},
        "register_scenes": {"replace": replace},
        "profile_scene": {"triggered": True},
        **extra,
    }


def episode_params(run_id: str, *, config: dict | None = None, replace: bool = False) -> dict:
    return {
        "build_recording_episodes": {
            "robot_run_id": run_id,
            "build_config": config or EPISODE_BUILD_CONFIG,
        },
        "register_episodes": {"replace": replace},
        "profile_episode": {"triggered": True},
    }
