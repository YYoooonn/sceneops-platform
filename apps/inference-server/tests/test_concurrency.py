"""Unit tests for inference concurrency control.

Tests cover:
1. Settings default (max_concurrent_inference_requests=1)
2. /readyz includes concurrency fields
3. /v1/detect serializes concurrent calls (semaphore)
4. active_requests recovers to 0 after exception
5. /v1/detect returns 503 when server not initialized
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

import inference_server.main as main_module
from inference_server.config import InferenceServerSettings
from inference_server.main import _ConcurrencyState, _WarmupState, app
from tests.conftest import make_mock_model, make_settings


# ── helpers ───────────────────────────────────────────────────────────────────


def _lifespan_client(
    mock_model: MagicMock,
    settings: InferenceServerSettings,
    *,
    raise_server_exceptions: bool = True,
):
    """Context manager: TestClient whose lifespan runs with given model/settings."""
    return (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    )


# ── 1. Settings default ───────────────────────────────────────────────────────


def test_settings_default_max_concurrent_is_1():
    settings = InferenceServerSettings.model_construct(
        **{
            k: v.default
            for k, v in InferenceServerSettings.model_fields.items()
            if hasattr(v, "default") and v.default is not None
        }
    )
    assert settings.max_concurrent_inference_requests == 1


# ── 2. /readyz includes concurrency fields ───────────────────────────────────


def test_readyz_includes_concurrency_fields():
    mock_model = make_mock_model()
    settings = make_settings(enable_warmup=False, max_concurrent_inference_requests=2)
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_concurrent_inference_requests"] == 2
    assert body["active_inference_requests"] == 0


# ── 3. Semaphore serializes concurrent requests ───────────────────────────────


async def test_semaphore_serializes_concurrent_detect():
    """Two concurrent requests with max_concurrent=1 are processed sequentially."""
    first_detect_started = threading.Event()
    first_detect_proceed = threading.Event()
    active_snapshots: list[int] = []
    lock = threading.Lock()

    def slow_detect(req):
        with lock:
            active_snapshots.append(main_module._concurrency.active_requests)
        first_detect_started.set()
        first_detect_proceed.wait(timeout=5)
        return [], 100.0

    mock_model = make_mock_model()
    mock_model.detect = MagicMock(side_effect=slow_detect)
    settings = make_settings(enable_warmup=False, max_concurrent_inference_requests=1)

    # Manually bootstrap state (no lifespan with ASGITransport).
    main_module._model = mock_model
    main_module._inference_sem = asyncio.Semaphore(1)
    main_module._concurrency = _ConcurrencyState(max_requests=1)
    main_module._warmup_state = _WarmupState(enabled=False)

    detect_payload = {
        "image_path": "/fake/image.jpg",
        "box_threshold": 0.35,
        "text_threshold": 0.25,
        "max_image_size": 800,
    }

    try:
        with patch("inference_server.main.get_settings", return_value=settings):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                # Start first request — will block inside slow_detect.
                task1 = asyncio.create_task(
                    client.post("/v1/detect", json=detect_payload)
                )

                # Wait until the first detect is actually running in the thread pool.
                await asyncio.to_thread(first_detect_started.wait, 5)

                # Semaphore should be fully acquired (value == 0).
                assert main_module._inference_sem._value == 0
                assert main_module._concurrency.active_requests == 1

                # Second request — blocked on semaphore until first completes.
                task2 = asyncio.create_task(
                    client.post("/v1/detect", json=detect_payload)
                )

                # One event-loop tick: task2 reaches the semaphore and suspends.
                await asyncio.sleep(0.05)

                # Only the first call has entered detect so far.
                assert mock_model.detect.call_count == 1

                # Release first request.
                first_detect_proceed.set()

                r1, r2 = await asyncio.gather(task1, task2)

                assert r1.status_code == 200
                assert r2.status_code == 200
                assert mock_model.detect.call_count == 2
                # Each call saw active_requests == 1: never more than 1 at a time.
                assert all(n == 1 for n in active_snapshots), active_snapshots
    finally:
        main_module._model = None
        main_module._inference_sem = None
        main_module._concurrency = None
        main_module._warmup_state = None


# ── 4. Active count recovers to 0 after exception ────────────────────────────


def test_active_count_zero_after_successful_detect():
    """active_requests must be 0 after a completed detect call."""
    mock_model = make_mock_model(detect_return=([], 50.0))
    settings = make_settings(enable_warmup=False)
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(
                "/v1/detect",
                json={"image_path": "/fake.jpg"},
            )
            assert resp.status_code == 200
            # Active count must return to 0 after the request completes.
            assert main_module._concurrency.active_requests == 0


def test_active_count_zero_after_detect_exception():
    """active_requests must return to 0 even when model.detect raises."""
    mock_model = make_mock_model(detect_raises=RuntimeError("CUDA OOM"))
    settings = make_settings(enable_warmup=False)
    with (
        patch("inference_server.main.GroundingDinoModel", return_value=mock_model),
        patch("inference_server.main.get_settings", return_value=settings),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            # RuntimeError is unhandled → 500; we just care about state recovery.
            client.post("/v1/detect", json={"image_path": "/fake.jpg"})
            # Concurrency state is still live (inside lifespan context).
            assert main_module._concurrency.active_requests == 0


# ── 5. /v1/detect returns 503 when server not initialized ────────────────────


def test_detect_503_when_server_not_initialized(client_no_lifespan):
    resp = client_no_lifespan.post(
        "/v1/detect",
        json={"image_path": "/fake.jpg"},
    )
    assert resp.status_code == 503
