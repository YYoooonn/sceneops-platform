"""GroundingDINO lidar reads are byte-verified (ADR-016): the backend decodes
only bytes whose size and sha256 equal the sample's pin, and a missing or
corrupt payload fails that sample's lifting, never the run and never silently."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from sceneops_core.artifacts.logical_uri import (
    LogicalNamespace,
    LogicalPayloadPin,
    format_logical_uri,
)
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.sample_views import (
    MemberPolicy,
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
    _load_lidar,
)
from sceneops_inference.detection.pointcloud2 import (
    POINTCLOUD2_CDR_MEDIA_TYPE,
    decode_pointcloud2_cdr_xyz,
)
from sceneops_recording import iter_recording_messages
from sceneops_recording.testing.ros2_recordings import LIDAR_TOPIC, default_recording
from sceneops_storage import (
    LocalArtifactStore,
    PayloadUriResolver,
    VerifiedPayloadReader,
)

MS = 1_000_000


class _UnverifiedReadForbidden:
    """An ArtifactStore stand-in for the request: any direct read is a bypass
    of the verified reader."""

    def __getattr__(self, name):
        raise AssertionError(f"lidar bytes read around the verified reader: {name}")


def _lidar_member():
    manifest = build_scene_manifest()
    view = build_scene_sample_view(
        scene=SceneRevisionRef(
            scene_id="scene-1",
            manifest_artifact_id="scene-manifest-1",
            manifest_checksum=manifest.checksum(),
        ),
        manifest=manifest,
        policy=SampleViewPolicy(
            anchor=SampleAnchorPolicy(channel="CAM_FRONT"),
            members=[MemberPolicy(channel="LIDAR_TOP", tolerance_ns=10 * MS)],
        ),
    )
    resolved = SceneSampleResolver(view, manifest).resolve(view.samples[0])
    lidar = resolved.member("LIDAR_TOP")
    payload = lidar.observation.payload.model_copy(
        update={"media_type": POINTCLOUD2_CDR_MEDIA_TYPE}
    )
    lidar = lidar.__class__(
        channel=lidar.channel,
        modality=lidar.modality,
        observation=lidar.observation.model_copy(update={"payload": payload}),
        calibration=lidar.calibration,
        time_delta_ns=lidar.time_delta_ns,
    )
    return resolved.anchor, lidar


@pytest.fixture()
def lidar_bytes(tmp_path) -> bytes:
    path = default_recording().write(tmp_path / "rec.mcap")
    return next(m.data for m in iter_recording_messages(path, topics=[LIDAR_TOPIC]))


@pytest.fixture()
def world(tmp_path):
    root = str(tmp_path / "artifacts")
    store = LocalArtifactStore(root_uri=root)
    resolver = PayloadUriResolver(
        {
            LogicalNamespace.OBSERVATION_PAYLOADS: f"{root}/observation_payloads",
            LogicalNamespace.SOURCES: f"{root}/sources",
        }
    )
    reader = VerifiedPayloadReader(store, resolver)
    camera, lidar = _lidar_member()

    async def sample(
        sample_id: str, data: bytes, *, stored: bytes | None
    ) -> DetectionSampleInput:
        uri = format_logical_uri(
            LogicalNamespace.OBSERVATION_PAYLOADS, f"run-001/{sample_id}"
        )
        if stored is not None:
            await store.write_bytes(resolver.resolve(uri), stored)
        return DetectionSampleInput(
            scene_id="scene-1",
            sample_id=sample_id,
            camera_channel="CAM_FRONT",
            camera_payload=LogicalPayloadPin(
                uri=format_logical_uri(
                    LogicalNamespace.OBSERVATION_PAYLOADS, f"run-001/{sample_id}-cam"
                ),
                checksum=sha256_checksum(b"jpeg"),
                size_bytes=4,
                media_type="image/jpeg",
            ),
            camera=camera,
            lidar=lidar,
            lidar_payload=LogicalPayloadPin(
                uri=uri,
                checksum=sha256_checksum(data),
                size_bytes=len(data),
                media_type=POINTCLOUD2_CDR_MEDIA_TYPE,
            ),
        )

    request = SimpleNamespace(
        payload_reader=reader, artifact_store=_UnverifiedReadForbidden()
    )
    return SimpleNamespace(sample=sample, request=request)


def _corrupt(data: bytes) -> bytes:
    flipped = bytearray(data)
    flipped[-1] ^= 0x01  # inside the point data: still decodable if unverified
    return bytes(flipped)


async def test_valid_lidar_decodes_exactly_as_before(world, lidar_bytes):
    sample = await world.sample("smp-valid", lidar_bytes, stored=lidar_bytes)
    xyz, error = await _load_lidar(world.request, sample)
    assert error is None
    np.testing.assert_array_equal(xyz, decode_pointcloud2_cdr_xyz(lidar_bytes))


async def test_corrupt_lidar_of_unchanged_size_is_a_sample_failure(world, lidar_bytes):
    corrupt = _corrupt(lidar_bytes)
    # The corruption would decode without verification.
    decode_pointcloud2_cdr_xyz(corrupt)
    sample = await world.sample("smp-corrupt", lidar_bytes, stored=corrupt)
    xyz, error = await _load_lidar(world.request, sample)
    assert xyz is None
    assert "the pin requires" in error


async def test_missing_lidar_is_a_sample_failure(world, lidar_bytes):
    sample = await world.sample("smp-missing", lidar_bytes, stored=None)
    xyz, error = await _load_lidar(world.request, sample)
    assert xyz is None
    assert "is not stored" in error


async def test_a_lidar_observation_without_a_pin_is_not_a_sample(world, lidar_bytes):
    sample = await world.sample("smp-valid", lidar_bytes, stored=lidar_bytes)
    with pytest.raises(ValueError, match="payload pin"):
        DetectionSampleInput(
            scene_id=sample.scene_id,
            sample_id=sample.sample_id,
            camera_channel=sample.camera_channel,
            camera_payload=sample.camera_payload,
            camera=sample.camera,
            lidar=sample.lidar,
        )


async def test_backend_marks_only_the_corrupt_sample_failed(
    world, lidar_bytes, monkeypatch
):
    async def detect(**_):
        return [
            {"bbox_2d": [10.0, 10.0, 60.0, 60.0], "score": 0.9, "category_name": "car"}
        ]

    monkeypatch.setattr(grounding_dino, "_call_inference_server", detect)
    valid = await world.sample("smp-valid", lidar_bytes, stored=lidar_bytes)
    corrupt = await world.sample(
        "smp-corrupt", lidar_bytes, stored=_corrupt(lidar_bytes)
    )
    config = SimpleNamespace(
        box_threshold=None,
        text_threshold=None,
        max_image_size=None,
        detection_prompt=None,
        camera_channel="CAM_FRONT",
        lidar_channel="LIDAR_TOP",
    )
    request = SimpleNamespace(
        input=SimpleNamespace(endpoint_url="http://inference", config=config),
        samples=[valid, corrupt],
        payload_reader=world.request.payload_reader,
        artifact_store=_UnverifiedReadForbidden(),
    )
    run = await GroundingDinoDetectionBackend().predict(request)
    by_sample = {p.sample.sample_id: p.predictions for p in run.samples}
    assert [p["lifting_status"] for p in by_sample["smp-valid"]] != ["failed"]
    assert by_sample["smp-valid"][0]["lifting_error"] is None
    (failed,) = by_sample["smp-corrupt"]
    assert failed["lifting_status"] == "failed"
    assert "the pin requires" in failed["lifting_error"]
    # The 2-D detection is kept, unlocalized, as for any undecodable lidar.
    assert (failed["bbox_2d"], failed["frame_id"]) == ([10.0, 10.0, 60.0, 60.0], None)
