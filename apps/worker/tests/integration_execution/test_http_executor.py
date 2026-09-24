"""Tests for HttpIntegrationExecutor (SceneOps V2 Request 4.6A): the
production IntegrationExecutor backend, calling an integration runtime's
POST /execute HTTP endpoint instead of spawning a sibling container.

Covers:
- the request body is IntegrationRequest.model_dump_json(by_alias=True),
  posted to <base_url><path> with extra_query_params as query params;
- a network/transport failure raises IntegrationExecutionError;
- a non-2xx response raises IntegrationExecutionError with the response's
  "detail" field (or raw text) in the message;
- invalid JSON / a payload that doesn't validate as IntegrationResult both
  raise IntegrationExecutionError;
- a successful 200 response returns the parsed, validated IntegrationResult.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRef
from sceneops_core.datasets.schemas.external import ExternalDatasetRef
from sceneops_core.integration_runtime import (
    CanonicalDatasetRef,
    IntegrationOperation,
    IntegrationRequest,
    IntegrationResult,
)

from sceneops_worker.integration_execution import (
    HttpIntegrationExecutor,
    HttpRuntimeConfig,
    IntegrationExecutionError,
)


def _request() -> IntegrationRequest:
    return IntegrationRequest(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
        ),
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
    )


def _result_json() -> str:
    result = IntegrationResult(
        operation=IntegrationOperation.INGEST,
        external_ref=ExternalDatasetRef(
            format="nuscenes", format_version="v1.0-mini", uri="/data/raw/nuscenes"
        ),
        canonical_ref=CanonicalDatasetRef(dataset_id="d", dataset_version="v1"),
        produced_artifacts={
            "raw_log_manifest": ArtifactRef(
                kind=ArtifactKind.RAW_LOG_MANIFEST, uri="s3://x/manifest.json"
            )
        },
    )
    return result.model_dump_json(by_alias=True)


def _config(**overrides) -> HttpRuntimeConfig:
    defaults = dict(
        base_url="http://nuscenes-integration:8080",
        extra_query_params={
            "raw_log_id": "log-1",
            "manifest_uri": "s3://x/manifest.json",
            "frame_index_uri": "s3://x/frames.json",
        },
    )
    defaults.update(overrides)
    return HttpRuntimeConfig(**defaults)


_RealAsyncClient = httpx.AsyncClient


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


# ── request construction ─────────────────────────────────────────────────────


class TestRequestConstruction:
    @pytest.mark.asyncio
    async def test_posts_body_and_query_params_to_base_url_plus_path(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, text=_result_json())

        config = _config()
        executor = HttpIntegrationExecutor(config)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _RealAsyncClient(transport=_mock_transport(handler), **kw),
            )
            result = await executor.execute(_request())

        assert captured["method"] == "POST"
        assert captured["url"].startswith("http://nuscenes-integration:8080/execute")
        assert "raw_log_id=log-1" in captured["url"]
        assert captured["body"] == json.loads(_request().model_dump_json(by_alias=True))
        assert isinstance(result, IntegrationResult)


# ── failure handling ──────────────────────────────────────────────────────────


class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_transport_error_raises_execution_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        config = _config()
        executor = HttpIntegrationExecutor(config)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _RealAsyncClient(transport=_mock_transport(handler), **kw),
            )
            with pytest.raises(IntegrationExecutionError, match="cannot reach"):
                await executor.execute(_request())

    @pytest.mark.asyncio
    async def test_non_2xx_raises_with_detail_field(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422, json={"detail": "unsupported operation 'export'"}
            )

        config = _config()
        executor = HttpIntegrationExecutor(config)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _RealAsyncClient(transport=_mock_transport(handler), **kw),
            )
            with pytest.raises(
                IntegrationExecutionError, match="unsupported operation 'export'"
            ):
                await executor.execute(_request())

    @pytest.mark.asyncio
    async def test_non_2xx_raises_with_raw_text_when_no_detail_field(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal server error")

        config = _config()
        executor = HttpIntegrationExecutor(config)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _RealAsyncClient(transport=_mock_transport(handler), **kw),
            )
            with pytest.raises(
                IntegrationExecutionError, match="internal server error"
            ):
                await executor.execute(_request())

    @pytest.mark.asyncio
    async def test_invalid_json_response_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="{not json")

        config = _config()
        executor = HttpIntegrationExecutor(config)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _RealAsyncClient(transport=_mock_transport(handler), **kw),
            )
            with pytest.raises(IntegrationExecutionError, match="invalid JSON"):
                await executor.execute(_request())

    @pytest.mark.asyncio
    async def test_result_failing_validation_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"operation": "not-a-real-operation"})

        config = _config()
        executor = HttpIntegrationExecutor(config)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _RealAsyncClient(transport=_mock_transport(handler), **kw),
            )
            with pytest.raises(IntegrationExecutionError, match="does not validate"):
                await executor.execute(_request())


# ── success path ──────────────────────────────────────────────────────────────


class TestSuccessPath:
    @pytest.mark.asyncio
    async def test_returns_validated_integration_result(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_result_json())

        config = _config()
        executor = HttpIntegrationExecutor(config)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                httpx,
                "AsyncClient",
                lambda **kw: _RealAsyncClient(transport=_mock_transport(handler), **kw),
            )
            result = await executor.execute(_request())

        assert result.produced_artifacts["raw_log_manifest"].uri == (
            "s3://x/manifest.json"
        )
