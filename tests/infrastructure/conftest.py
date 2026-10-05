"""Fixtures for the infrastructure acceptance tests (see infra_support)."""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from infra_support import API_BASE_URL, API_PREFIX, REPO_ROOT, Api


@pytest.fixture(scope="session")
def api() -> Api:
    client = httpx.Client(base_url=f"{API_BASE_URL}{API_PREFIX}", timeout=60.0)
    try:
        client.get("/pipelines/definitions").raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(f"API not reachable at {API_BASE_URL} ({exc}); run `make local-up`")
    yield Api(client)
    client.close()


@pytest.fixture(scope="session")
def baseline(api: Api) -> dict:
    """The canonical L1/L2 baseline (create-or-verify), as its JSON summary."""
    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "canonical" / "canonical_bootstrap.sh")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])
