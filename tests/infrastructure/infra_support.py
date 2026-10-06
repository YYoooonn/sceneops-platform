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
otherwise), take one of them (`baseline_run`) and build into their own
`sceneops-test-infra-*` DatasetVersions: no RobotRun is created and the reference
DatasetVersion is never mutated. Those DatasetVersions are test residue that a runtime
reset (`make local-reset`) drops.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_PREFIX = os.environ.get("API_PREFIX", "/api/v1")
TERMINAL = {"succeeded", "failed", "blocked", "cancelled"}


def _config(name: str) -> dict:
    return json.loads((REPO_ROOT / "config" / "baselines" / name).read_text())


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

    def new_dataset_version(self, prefix: str = "infra") -> tuple[str, str]:
        dataset_id = f"sceneops-test-infra-{prefix}"
        version = f"v-{uuid.uuid4().hex[:10]}"
        self.post("/datasets", {"dataset_id": dataset_id, "name": "Infrastructure tests"})
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
        assert self.dispatch(created["pipelineRunId"]).status_code in (200, 202)
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
