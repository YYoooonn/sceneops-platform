"""Tests for GroundingDINO worker adapter HTTP request contract."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from sceneops_worker.inference.detection.grounding_dino import _call_inference_server


# ── _call_inference_server ────────────────────────────────────────────────────


async def test_request_payload_contains_image_uri():
    """Adapter sends image_uri in the JSON payload."""
    image_uri = "file:///data/raw/nuscenes/samples/CAM_FRONT/n008-xxx.jpg"
    captured: list[dict] = []

    async def fake_post(url, *, json=None, **kwargs):
        captured.append(json or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"detections": []}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)

    await _call_inference_server(
        client=client,
        endpoint_url="http://sceneops-inference:8001",
        image_uri=image_uri,
        box_threshold=0.35,
        text_threshold=0.25,
        max_image_size=800,
        detection_prompt="car . person .",
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload["image_uri"] == image_uri


async def test_request_payload_does_not_contain_image_path():
    """Adapter must not fall back to legacy image_path field."""
    captured: list[dict] = []

    async def fake_post(url, *, json=None, **kwargs):
        captured.append(json or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"detections": []}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)

    await _call_inference_server(
        client=client,
        endpoint_url="http://sceneops-inference:8001",
        image_uri="file:///data/raw/img.jpg",
        box_threshold=0.35,
        text_threshold=0.25,
        max_image_size=800,
        detection_prompt=None,
    )

    payload = captured[0]
    assert "image_path" not in payload


async def test_request_payload_includes_thresholds_and_prompt():
    captured: list[dict] = []

    async def fake_post(url, *, json=None, **kwargs):
        captured.append(json or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"detections": []}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)

    await _call_inference_server(
        client=client,
        endpoint_url="http://sceneops-inference:8001",
        image_uri="file:///data/raw/img.jpg",
        box_threshold=0.4,
        text_threshold=0.3,
        max_image_size=640,
        detection_prompt="car . truck .",
    )

    payload = captured[0]
    assert payload["box_threshold"] == 0.4
    assert payload["text_threshold"] == 0.3
    assert payload["max_image_size"] == 640
    assert payload["prompt"] == "car . truck ."


async def test_request_omits_prompt_when_none():
    captured: list[dict] = []

    async def fake_post(url, *, json=None, **kwargs):
        captured.append(json or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"detections": []}
        return resp

    client = MagicMock()
    client.post = AsyncMock(side_effect=fake_post)

    await _call_inference_server(
        client=client,
        endpoint_url="http://sceneops-inference:8001",
        image_uri="file:///data/raw/img.jpg",
        box_threshold=0.35,
        text_threshold=0.25,
        max_image_size=800,
        detection_prompt=None,
    )

    payload = captured[0]
    assert "prompt" not in payload
