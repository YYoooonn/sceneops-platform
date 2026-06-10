"""Shared test fixtures and helpers for inference-server tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import inference_server.main as main_module
from inference_server.config import InferenceServerSettings
from inference_server.main import app


def make_settings(**overrides) -> InferenceServerSettings:
    """Build InferenceServerSettings with test-safe defaults."""
    defaults = dict(
        host="0.0.0.0",
        port=8001,
        model_id="IDEA-Research/grounding-dino-tiny",
        hf_cache_dir=None,
        detection_prompt="car . person . barrier .",
        box_threshold=0.35,
        text_threshold=0.25,
        max_image_size=800,
        enable_warmup=False,
        require_warmup_success_for_ready=False,
        warmup_image_size=64,
        warmup_text_prompt="car.",
        max_concurrent_inference_requests=1,
    )
    defaults.update(overrides)
    return InferenceServerSettings.model_construct(**defaults)


def make_mock_model(
    *,
    warmup_raises: Exception | None = None,
    detect_raises: Exception | None = None,
    detect_return: tuple = ([], 10.0),
) -> MagicMock:
    m = MagicMock()
    m.device = "cpu"
    m.load = MagicMock()
    m.warmup = MagicMock(
        side_effect=warmup_raises if warmup_raises is not None else None,
        return_value=None,
    )
    if detect_raises is not None:
        m.detect = MagicMock(side_effect=detect_raises)
    else:
        m.detect = MagicMock(return_value=detect_return)
    return m


@pytest.fixture()
def client_no_lifespan():
    """TestClient without lifespan — all globals stay None. Use for not-ready scenarios."""
    saved = (
        main_module._model,
        main_module._warmup_state,
        main_module._inference_sem,
        main_module._concurrency,
    )
    main_module._model = None
    main_module._warmup_state = None
    main_module._inference_sem = None
    main_module._concurrency = None
    yield TestClient(app, raise_server_exceptions=False)
    (
        main_module._model,
        main_module._warmup_state,
        main_module._inference_sem,
        main_module._concurrency,
    ) = saved
