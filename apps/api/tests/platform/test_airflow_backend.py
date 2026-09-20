"""Unit tests for AirflowPipelineExecutionBackend.dispatch_pipeline.

Uses httpx.MockTransport so no real HTTP call is made — asserts the request
shape (URL, dag_run_id, conf) and the returned ExecutionDispatchResult.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.platform.executions.backends.airflow import AirflowPipelineExecutionBackend
from sceneops_core.executions.schemas import (
    ExecutionBackend,
    ExecutionKind,
    ExecutionStatus,
)

PIPELINE_RUN_ID = "pipe-001"

_RealAsyncClient = httpx.AsyncClient


def _patch_transport(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return _RealAsyncClient(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)


async def test_dispatch_pipeline_posts_dag_run_id_and_conf(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth_header"] = request.headers.get("authorization")
        return httpx.Response(200, json={"dag_run_id": PIPELINE_RUN_ID})

    _patch_transport(monkeypatch, handler)

    backend = AirflowPipelineExecutionBackend(
        base_url="http://airflow-webserver:8080",
        pipeline_dag_id="sceneops_pipeline_run",
        username="airflow",
        password="airflow",
    )

    result = await backend.dispatch_pipeline(PIPELINE_RUN_ID)

    assert captured["method"] == "POST"
    assert captured["url"] == (
        "http://airflow-webserver:8080/api/v1/dags/sceneops_pipeline_run/dagRuns"
    )
    assert captured["auth_header"] is not None  # basic auth header present
    assert result.execution_backend == ExecutionBackend.AIRFLOW
    assert result.execution_kind == ExecutionKind.PIPELINE_RUN
    assert result.resource_id == PIPELINE_RUN_ID
    assert result.status == ExecutionStatus.QUEUED
    assert result.external_id == PIPELINE_RUN_ID
    assert result.execution_id  # non-empty


async def test_dispatch_pipeline_sends_pipeline_run_id_in_body_and_conf(monkeypatch):
    captured_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json={})

    _patch_transport(monkeypatch, handler)

    backend = AirflowPipelineExecutionBackend(
        base_url="http://airflow-webserver:8080",
        pipeline_dag_id="sceneops_pipeline_run",
    )

    await backend.dispatch_pipeline(PIPELINE_RUN_ID)

    assert captured_body["dag_run_id"] == PIPELINE_RUN_ID
    assert captured_body["conf"]["pipeline_run_id"] == PIPELINE_RUN_ID


async def test_dispatch_pipeline_without_username_sends_no_auth_header(monkeypatch):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth_header"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    _patch_transport(monkeypatch, handler)

    backend = AirflowPipelineExecutionBackend(
        base_url="http://airflow-webserver:8080",
        pipeline_dag_id="sceneops_pipeline_run",
    )

    await backend.dispatch_pipeline(PIPELINE_RUN_ID)

    assert captured["auth_header"] is None


async def test_dispatch_pipeline_raises_on_error_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "dag not found"})

    _patch_transport(monkeypatch, handler)

    backend = AirflowPipelineExecutionBackend(
        base_url="http://airflow-webserver:8080",
        pipeline_dag_id="sceneops_pipeline_run",
    )

    with pytest.raises(httpx.HTTPStatusError):
        await backend.dispatch_pipeline(PIPELINE_RUN_ID)
