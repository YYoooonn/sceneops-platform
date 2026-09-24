"""Tests for InProcessIntegrationExecutor (SceneOps V2 Request 4.6 §4):
tests/dev-only IntegrationExecutor backend that calls an already-bound
runtime function directly instead of running a container."""

from __future__ import annotations

import pytest
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
    IntegrationResult,
)

from sceneops_worker.integration_execution import InProcessIntegrationExecutor


def _request() -> IntegrationRequest:
    return IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
        ),
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
    )


@pytest.mark.asyncio
async def test_execute_delegates_to_bound_runner() -> None:
    received: list[IntegrationRequest] = []

    async def _run(request: IntegrationRequest) -> IntegrationResult:
        received.append(request)
        return IntegrationResult(
            operation=IntegrationOperation.INGEST,
            external_ref=request.external_ref,
            canonical_ref=request.canonical_ref,
            produced_artifacts={
                "raw_log_manifest": ArtifactRef(
                    kind=ArtifactKind.RAW_LOG_MANIFEST, uri="s3://x/manifest.json"
                )
            },
            result_metadata={"ok": True},
        )

    executor = InProcessIntegrationExecutor(_run)
    request = _request()
    result = await executor.execute(request)

    assert received == [request]
    assert result.result_metadata == {"ok": True}
