"""Helpers for the infrastructure acceptance tests (client, params builders).

These run against the disposable execution runtime that `make test-infrastructure`
starts on a disposable PostgreSQL database and MinIO bucket (execution_runtime.py): the
FastAPI control plane, the pipeline and job Celery workers, Redis, PostgreSQL and
MinIO. They exercise the contracts of the pipelines that no single user journey
proves: dedup, force, retry, replacement, blocked resumption, failure recovery,
concurrent registration, and every pipeline task running as a Job on the job workers.

The pipelines need a registered RobotRun. The runtime starts empty, so the suite's
`baseline` fixture seeds the one it consumes: the golden reference contract's Recording
Import RobotRun (`tools/baselines/canonical/canonical_bootstrap.sh`, create-or-verify; the
smoke-1 selection, scene-0061, unless REFERENCE_SCOPE says otherwise), published from
the locked recording into the disposable bucket. `baseline_run` takes one of them and
the tests build into DatasetVersions they own: the reference environment is never read
or written.

Test-owned identity is still fixed and named after the test: `sceneops-test-infra-pipelines`
holds one DatasetVersion per test. Everything the tests append (PipelineRuns, Jobs, the job-keyed
validation / profile reports of a re-executed pipeline, duplicate ArtifactRecords of a
re-executed evaluation) is execution history the platform keeps by design; it lives in the
disposable database and bucket and is dropped with them. No test deletes anything.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_PREFIX = os.environ.get("API_PREFIX", "/api/v1")
TERMINAL = {"succeeded", "failed", "blocked", "cancelled"}
JOB_TERMINAL = {"succeeded", "failed", "cancelled", "skipped"}


def _config(name: str) -> dict:
    return json.loads((REPO_ROOT / "config" / "baselines" / name).read_text())


INFRA_PIPELINES_DATASET = "sceneops-test-infra-pipelines"

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

    def dispatched(self, run_id: str) -> None:
        """Dispatch and fail fast unless the API accepted it. The create request's write
        is committed before its response is sent, so a rejection is never a read of a
        write that is not yet visible."""
        response = self.dispatch(run_id)
        assert response.status_code in (200, 202), (
            f"POST /pipelines/runs/{run_id}/execute -> {response.status_code} {response.text}"
        )

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
        self.dispatched(created["pipelineRunId"])
        return self.wait(created["pipelineRunId"])

    def tasks(self, run_id: str) -> dict[str, dict]:
        return {t["pipelineTaskId"]: t for t in self.get(f"/pipelines/runs/{run_id}/tasks")["tasks"]}

    def scenes(self, dataset: tuple[str, str]) -> list[dict]:
        return self.get("/scenes", dataset_id=dataset[0], dataset_version=dataset[1], limit=500)["scenes"]

    def episodes(self, dataset: tuple[str, str]) -> list[dict]:
        return self.get("/episodes", dataset_id=dataset[0], dataset_version=dataset[1], limit=500)[
            "episodes"
        ]


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
