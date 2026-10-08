"""Fixtures for the infrastructure acceptance tests (see infra_support)."""

from __future__ import annotations

import json
import os
import subprocess

import httpx
import pytest

from infra_support import API_BASE_URL, API_PREFIX, REPO_ROOT, Api
from recovery_support import (  # noqa: F401  (fixtures of the recovery suites)
    clean_faults_and_queue,
    env,
    fresh_engine,
)


@pytest.fixture(scope="session")
def api() -> Api:
    # These tests re-execute pipelines on purpose and the platform keeps every Job and
    # PipelineRun that results, so they run only against the disposable execution
    # runtime a make target started for them -- never a developer's API on :8000.
    assert os.environ.get("SCENEOPS_EXECUTION_RUNTIME") == "disposable", (
        "the infrastructure tests run on a disposable execution runtime: "
        "`make test-infrastructure`"
    )
    client = httpx.Client(base_url=f"{API_BASE_URL}{API_PREFIX}", timeout=60.0)
    try:
        client.get("/pipelines/definitions").raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"API not reachable at {API_BASE_URL} ({exc}); run `make test-infrastructure`")
    yield Api(client)
    client.close()


@pytest.fixture(scope="session")
def baseline(api: Api) -> dict:
    """The canonical L1/L2 baseline (create-or-verify), as its JSON summary."""
    result = subprocess.run(
        [str(REPO_ROOT / "tools" / "baselines" / "canonical" / "canonical_bootstrap.sh")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="session")
def baseline_run(baseline: dict) -> dict:
    """One RobotRun of the baseline, with that run's own facts (``robot_run_id``,
    ``fixture_id``, ``source_unit``, ``scene_count``, ``episode_count``).

    Pipelines build per RobotRun, so every expectation about a build is stated
    against this run, never against the baseline's totals. The first fixture of
    the selection is used; INFRASTRUCTURE_FIXTURE names another."""
    by_fixture = {f["fixture_id"]: f for f in baseline["fixtures"]}
    wanted = os.environ.get("INFRASTRUCTURE_FIXTURE") or next(iter(by_fixture))
    assert wanted in by_fixture, (
        f"{wanted!r} is not a fixture of the baseline {sorted(by_fixture)}"
    )
    return by_fixture[wanted]
