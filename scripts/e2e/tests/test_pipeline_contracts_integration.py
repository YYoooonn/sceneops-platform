"""Live-API integration test for pipeline-definition/pipeline-run-creation
contracts.

Exercises the pipeline-definitions registry and the pipeline-run-creation
request-validation boundary -- pure API/DB concerns that need no nuScenes
data and no worker execution.

Requires a real, running `api` service (`make local-up`) -- skips (not
fails) if API_BASE_URL is unreachable, matching every other
test-integration convention in this repository (real-infra-required,
skip-don't-fail-if-absent).
"""

from __future__ import annotations

import os

import httpx
import pytest

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
API_PREFIX = os.environ.get("API_PREFIX", "/api/v1")

SUPPORTED_PIPELINE_TYPES = ("detection_evaluation",)
# Experimental (hidden from the default listing): the legacy Scene producers.
HIDDEN_PIPELINE_TYPES = ("dataset_scene_ingestion", "raw_log_scene_building")
REMOVED_PIPELINE_TYPES = ("scene_registration",)


@pytest.fixture(scope="module")
def api_client():
    client = httpx.Client(base_url=f"{API_BASE_URL}{API_PREFIX}", timeout=10.0)
    try:
        response = client.get("/pipelines/definitions")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.skip(
            f"API not reachable at {API_BASE_URL} ({exc}) — pipeline-contracts "
            "integration tests need a running `make local-up` stack."
        )
    yield client
    client.close()


def test_pipeline_definitions_list_only_supported_and_implemented(api_client):
    response = api_client.get("/pipelines/definitions")
    response.raise_for_status()
    body = response.json()

    assert body["count"] == len(SUPPORTED_PIPELINE_TYPES), (
        f"expected exactly {len(SUPPORTED_PIPELINE_TYPES)} supported pipeline "
        f"definitions, got {body['count']}: "
        f"{[d['type'] for d in body['definitions']]}"
    )

    definitions_by_type = {d["type"]: d for d in body["definitions"]}
    for pipeline_type in SUPPORTED_PIPELINE_TYPES:
        assert pipeline_type in definitions_by_type, (
            f"expected supported pipeline '{pipeline_type}' in definitions listing"
        )

    for pipeline_type in HIDDEN_PIPELINE_TYPES + REMOVED_PIPELINE_TYPES:
        assert pipeline_type not in definitions_by_type, (
            f"pipeline '{pipeline_type}' must NOT appear in the default "
            "definitions listing"
        )


def test_removed_pipeline_creation_rejected(api_client):
    dataset_id = "test-e2e-pipeline-contracts-unsupported"
    dataset_version = "test-v1"

    # A Dataset row isn't required for the rejection itself (request-shape
    # validation happens before any dataset lookup), but upserting one keeps
    # this test's request payload identical to a real caller's, rather than
    # relying on rejection-before-lookup ordering as an implicit assumption.
    api_client.post(
        "/datasets",
        json={"dataset_id": dataset_id, "name": "pipeline-contracts test", "metadata": {}},
    )

    for pipeline_type in REMOVED_PIPELINE_TYPES:
        response = api_client.post(
            "/pipelines/runs",
            json={
                "type": pipeline_type,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
            },
        )
        assert response.status_code in (400, 422), (
            f"expected HTTP 400/422 for removed pipeline '{pipeline_type}', "
            f"got {response.status_code}: {response.text}"
        )
