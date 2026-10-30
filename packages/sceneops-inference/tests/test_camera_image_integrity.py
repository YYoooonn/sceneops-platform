"""GroundingDINO camera images are byte-verified by the inference server: the
backend sends each image's physical location with its pin's sha256 and size, the
server decodes only matching bytes, and a missing, mismatched or undecodable
image fails the run with a typed error, never as empty detections."""

from __future__ import annotations

import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

import inference_server.main as server
from inference_server.image_resolver import ImageResolver
from sceneops_core.artifacts.logical_uri import (
    LogicalNamespace,
    LogicalPayloadPin,
    format_logical_uri,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.sample_views import (
    SampleAnchorPolicy,
    SampleViewPolicy,
    SceneRevisionRef,
    SceneSampleResolver,
    build_scene_sample_view,
)
from sceneops_core.scenes.testing import build_scene_manifest
from sceneops_inference.detection import grounding_dino
from sceneops_inference.detection.base import DetectionSampleInput
from sceneops_inference.detection.grounding_dino import (
    GroundingDinoDetectionBackend,
    ImageDecodeError,
    _call_inference_server,
)
from sceneops_storage import (
    LocalArtifactStore,
    PayloadMissingError,
    PayloadUriResolver,
    PayloadVerificationError,
    VerifiedPayloadReader,
)

ENDPOINT = "http://sceneops-inference:8001"


def _camera_member():
    manifest = build_scene_manifest()
    view = build_scene_sample_view(
        scene=SceneRevisionRef(
            scene_id="scene-1",
            manifest_artifact_id="scene-manifest-1",
            manifest_checksum=manifest.checksum(),
        ),
        manifest=manifest,
        policy=SampleViewPolicy(anchor=SampleAnchorPolicy(channel="CAM_FRONT")),
    )
    return SceneSampleResolver(view, manifest).resolve(view.samples[0]).anchor


def _png(color=0) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 6), color=(color, color, color)).save(buffer, format="PNG")
    return buffer.getvalue()


def _pin(namespace: LogicalNamespace, key: str, data: bytes) -> LogicalPayloadPin:
    return LogicalPayloadPin(
        uri=format_logical_uri(namespace, key),
        checksum=sha256_checksum(data),
        size_bytes=len(data),
        media_type="image/png",
    )


def _sample(sample_id: str, camera_payload: LogicalPayloadPin) -> DetectionSampleInput:
    return DetectionSampleInput(
        scene_id="scene-1",
        sample_id=sample_id,
        camera_channel="CAM_FRONT",
        camera_payload=camera_payload,
        camera=_camera_member(),
    )


def _config():
    return SimpleNamespace(
        box_threshold=None,
        text_threshold=None,
        max_image_size=None,
        detection_prompt=None,
        camera_channel="CAM_FRONT",
        lidar_channel=None,
    )


class _UnverifiedReadForbidden:
    """The request's ArtifactStore: the backend never reads camera bytes."""

    def __getattr__(self, name):
        raise AssertionError(f"camera bytes read by the worker: {name}")


def _request(samples, reader: VerifiedPayloadReader, **stores):
    return SimpleNamespace(
        input=SimpleNamespace(endpoint_url=ENDPOINT, config=_config()),
        samples=samples,
        payload_reader=reader,
        artifact_store=_UnverifiedReadForbidden(),
        **stores,
    )


# ── the request carries the pin; the location is resolved at the boundary ────


@pytest.mark.parametrize(
    "roots, expected",
    [
        (
            {
                LogicalNamespace.OBSERVATION_PAYLOADS: "/data/artifacts/observation_payloads",
                LogicalNamespace.SOURCES: "/data/artifacts/sources",
            },
            "file:///data/artifacts/observation_payloads/run-001/cam-1",
        ),
        (
            {
                LogicalNamespace.OBSERVATION_PAYLOADS: "s3://sceneops/artifacts/observation_payloads",
                LogicalNamespace.SOURCES: "s3://sceneops/artifacts/sources",
            },
            "s3://sceneops/artifacts/observation_payloads/run-001/cam-1",
        ),
    ],
)
async def test_recording_image_is_sent_with_its_pin(monkeypatch, roots, expected):
    data = _png()
    pin = _pin(LogicalNamespace.OBSERVATION_PAYLOADS, "run-001/cam-1", data)
    calls: list[dict] = []

    async def detect(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(grounding_dino, "_call_inference_server", detect)
    reader = VerifiedPayloadReader(MagicMock(), PayloadUriResolver(roots))
    run = await GroundingDinoDetectionBackend().predict(
        _request([_sample("smp-1", pin)], reader)
    )

    (call,) = calls
    assert (call["image_uri"], call["image_checksum"], call["image_size_bytes"]) == (
        expected,
        sha256_checksum(data),
        len(data),
    )
    assert run.samples[0].metadata["image_uri"] == expected


async def test_native_dataset_image_uses_the_same_contract(monkeypatch):
    data = _png()
    key = "contents/content-1/files/samples/CAM_FRONT/a?b#c.jpg"
    pin = _pin(LogicalNamespace.SOURCES, key, data)
    calls: list[dict] = []

    async def detect(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(grounding_dino, "_call_inference_server", detect)
    reader = VerifiedPayloadReader(
        MagicMock(),
        PayloadUriResolver(
            {
                LogicalNamespace.OBSERVATION_PAYLOADS: "s3://sceneops/artifacts/observation_payloads",
                LogicalNamespace.SOURCES: "s3://sceneops/artifacts/sources",
            }
        ),
    )
    await GroundingDinoDetectionBackend().predict(
        _request([_sample("smp-1", pin)], reader)
    )

    (call,) = calls
    # The physical key, verbatim; no Logical URI, content id or source kind.
    assert call["image_uri"] == f"s3://sceneops/artifacts/sources/{key}"
    assert (call["image_checksum"], call["image_size_bytes"]) == (
        pin.checksum,
        pin.size_bytes,
    )


async def test_request_body_is_location_pin_and_parameters_only():
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"detections": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _call_inference_server(
            client=client,
            endpoint_url=ENDPOINT,
            image_uri="file:///data/artifacts/observation_payloads/run-001/cam-1",
            image_checksum="sha256:" + "ab" * 32,
            image_size_bytes=10,
            box_threshold=0.35,
            text_threshold=0.25,
            max_image_size=800,
            detection_prompt=None,
        )

    assert set(captured[0]) == {
        "image_uri",
        "image_checksum",
        "image_size_bytes",
        "box_threshold",
        "text_threshold",
        "max_image_size",
    }


# ── server failures become typed errors, once ─────────────────────────────────


@pytest.mark.parametrize(
    "status, body, error",
    [
        (
            422,
            {"detail": {"code": "image_integrity_mismatch", "message": "m"}},
            PayloadVerificationError,
        ),
        (
            422,
            {"detail": {"code": "image_not_found", "message": "m"}},
            PayloadMissingError,
        ),
        (
            422,
            {"detail": {"code": "image_decode_failed", "message": "m"}},
            ImageDecodeError,
        ),
        (
            403,
            {"detail": {"code": "image_access_denied", "message": "m"}},
            httpx.HTTPStatusError,
        ),
        (422, {"detail": [{"loc": ["body", "image_checksum"]}]}, httpx.HTTPStatusError),
        (500, {"detail": "CUDA OOM"}, httpx.HTTPStatusError),
    ],
)
async def test_failed_request_raises_its_type_without_retry(status, body, error):
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(error):
            await _call_inference_server(
                client=client,
                endpoint_url=ENDPOINT,
                image_uri="file:///img.png",
                image_checksum="sha256:" + "ab" * 32,
                image_size_bytes=10,
                box_threshold=0.35,
                text_threshold=0.25,
                max_image_size=800,
                detection_prompt=None,
            )
    assert len(posts) == 1


# ── worker client against the real server, in process ─────────────────────────


@pytest.fixture()
def served(tmp_path, monkeypatch):
    """The inference server app with its real ImageResolver over a local
    artifact root and a mock model, reached by the backend's own HTTP client."""
    root = tmp_path / "artifacts"
    resolver = PayloadUriResolver(
        {
            LogicalNamespace.OBSERVATION_PAYLOADS: f"{root}/observation_payloads",
            LogicalNamespace.SOURCES: f"{root}/sources",
        }
    )
    store = LocalArtifactStore(root_uri=str(root))
    model = MagicMock(device="cpu")
    model.detect_image = MagicMock(
        return_value=(
            [{"category_name": "car", "score": 0.9, "bbox_2d": [1.0, 1.0, 4.0, 4.0]}],
            2.0,
        )
    )
    import asyncio

    monkeypatch.setattr(server, "_model", model)
    monkeypatch.setattr(
        server, "_image_resolver", ImageResolver(allowed_roots=[str(root)])
    )
    monkeypatch.setattr(server, "_inference_sem", asyncio.Semaphore(1))
    monkeypatch.setattr(server, "_concurrency", server._ConcurrencyState(1))
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: SimpleNamespace(
            box_threshold=0.35, text_threshold=0.25, max_image_size=800
        ),
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        grounding_dino.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.ASGITransport(app=server.app), **kw),
    )

    async def store_image(key: str, stored: bytes | None, pinned: bytes):
        pin = _pin(LogicalNamespace.OBSERVATION_PAYLOADS, key, pinned)
        if stored is not None:
            await store.write_bytes(resolver.resolve(pin.uri), stored)
        return pin

    return SimpleNamespace(
        model=model,
        reader=VerifiedPayloadReader(store, resolver),
        store_image=store_image,
    )


async def test_valid_image_is_detected_as_before(served):
    data = _png(color=7)
    pin = await served.store_image("run-001/cam-ok", data, data)

    run = await GroundingDinoDetectionBackend().predict(
        _request([_sample("smp-ok", pin)], served.reader)
    )

    (prediction,) = run.samples[0].predictions
    assert (prediction["bbox_2d"], prediction["score"]) == ([1.0, 1.0, 4.0, 4.0], 0.9)
    assert prediction["lifting_status"] == "not_applicable"
    image = served.model.detect_image.call_args.args[0]
    assert (image.size, image.getpixel((0, 0))) == ((8, 6), (7, 7, 7))
    assert run.inference_request_count == 1


async def test_corrupt_image_fails_the_run_without_invoking_the_model(served):
    data = _png()
    corrupt = bytearray(data)
    corrupt[-1] ^= 0x01
    pin = await served.store_image("run-001/cam-bad", bytes(corrupt), data)
    run_store, derived_store = AsyncMock(), AsyncMock()

    with pytest.raises(PayloadVerificationError, match="the pin requires"):
        await GroundingDinoDetectionBackend().run(
            _request(
                [_sample("smp-bad", pin)],
                served.reader,
                run_artifact_store=run_store,
                derived_store=derived_store,
            )
        )

    served.model.detect_image.assert_not_called()
    # Nothing is published: no empty shard, no prediction manifest.
    run_store.write_prediction_shard.assert_not_called()
    derived_store.publish.assert_not_called()


async def test_missing_image_fails_the_run_without_invoking_the_model(served):
    pin = await served.store_image("run-001/cam-missing", None, _png())

    with pytest.raises(PayloadMissingError, match="Image not found"):
        await GroundingDinoDetectionBackend().predict(
            _request([_sample("smp-missing", pin)], served.reader)
        )
    served.model.detect_image.assert_not_called()


async def test_one_corrupt_image_fails_a_run_of_valid_ones(served):
    data = _png()
    valid = await served.store_image("run-001/cam-ok", data, data)
    wrong = await served.store_image("run-001/cam-wrong", _png(color=9), data)

    with pytest.raises(PayloadVerificationError):
        await GroundingDinoDetectionBackend().predict(
            _request(
                [_sample("smp-ok", valid), _sample("smp-wrong", wrong)], served.reader
            )
        )
    assert served.model.detect_image.call_count == 1


def test_pin_checksum_is_the_full_object_sha256():
    data = _png()
    assert sha256_checksum(data) == f"sha256:{hashlib.sha256(data).hexdigest()}"
