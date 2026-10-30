"""The unified PayloadRef (ADR-017): Logical URI, full-object integrity, media
type and selector, the same for every source."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from sceneops_core.artifacts.logical_uri import LogicalPayloadPin, PinnedPayload
from sceneops_core.artifacts.schemas import PayloadRef, VideoFrameSelector
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.sources import content_id_for

CHECKSUM = "sha256:" + "ab" * 32
CONTENT_ID = content_id_for("sha256:" + "c" * 64)
PAYLOAD_URI = "sceneops://observation-payloads/run-001/payload-0001"
FILE_URI = (
    f"sceneops://sources/contents/{CONTENT_ID}/files/samples/CAM_FRONT/"
    "n015%2B0800__CAM_FRONT__1532402927612460.jpg"
)
VIDEO_URI = (
    f"sceneops://sources/contents/{CONTENT_ID}/files/videos/observation.image/"
    "chunk-000/file-000.mp4"
)


def _ref(**overrides) -> dict:
    return {
        "uri": PAYLOAD_URI,
        "checksum": CHECKSUM,
        "size_bytes": 15,
        "media_type": "image/jpeg",
        **overrides,
    }


# --- the model ---------------------------------------------------------------------


@pytest.mark.parametrize("uri", [PAYLOAD_URI, FILE_URI, VIDEO_URI])
def test_a_canonical_logical_uri_is_accepted_verbatim(uri):
    ref = PayloadRef.model_validate(_ref(uri=uri))
    assert ref.uri == uri
    assert str(ref.logical_uri()) == uri


@pytest.mark.parametrize(
    "uri",
    [
        "s3://bucket/observation-payloads/run-001/payload-0001",
        "file:///data/payloads/run-001/payload-0001",
        "sceneops://elsewhere/run-001/payload-0001",
        "sceneops://observation-payloads/",
        "sceneops://observation-payloads/run-001//payload-0001",
        "sceneops://observation-payloads/run-001/../payload-0001",
        "sceneops://sources/contents/a+b.jpg",
        "sceneops://sources/contents/a%2bb.jpg",
        "sceneops://sources/contents/%61.jpg",
    ],
)
def test_anything_but_a_canonical_logical_uri_is_refused(uri):
    with pytest.raises(ValidationError):
        PayloadRef.model_validate(_ref(uri=uri))


@pytest.mark.parametrize(
    "checksum",
    [
        "sha256:" + "AB" * 32,
        "sha256:" + "ab" * 31,
        "ab" * 32,
        "md5:" + "ab" * 16,
        "sha256:" + "ab" * 32 + "\n",
    ],
)
def test_the_checksum_is_the_sha256_contract(checksum):
    with pytest.raises(ValidationError):
        PayloadRef.model_validate(_ref(checksum=checksum))


@pytest.mark.parametrize("size", [0, 1, 2**40])
def test_zero_and_positive_sizes_are_accepted(size):
    assert PayloadRef.model_validate(_ref(size_bytes=size)).size_bytes == size


@pytest.mark.parametrize("size", [-1, 1.0, 1.5, "15", True, None])
def test_negative_and_non_integer_sizes_are_refused(size):
    with pytest.raises(ValidationError):
        PayloadRef.model_validate(_ref(size_bytes=size))


@pytest.mark.parametrize(
    "media_type",
    ["image/jpeg", "video/mp4", "application/x.nuscenes.lidar-pcd-bin"],
)
def test_media_types_are_validated_not_normalized(media_type):
    assert PayloadRef.model_validate(_ref(media_type=media_type)).media_type == (
        media_type
    )


@pytest.mark.parametrize(
    "media_type", ["Image/JPEG", "image/jpeg; q=1", "jpeg", "", "image/"]
)
def test_invalid_media_types_are_refused(media_type):
    with pytest.raises(ValidationError):
        PayloadRef.model_validate(_ref(media_type=media_type))


def test_a_video_frame_selector_names_a_time_in_the_videos_own_time_base():
    ref = PayloadRef.model_validate(
        _ref(
            uri=VIDEO_URI,
            media_type="video/mp4",
            selector={"kind": "video_frame", "timestamp_ns": 100_000_000},
        )
    )
    assert ref.selector == VideoFrameSelector(timestamp_ns=100_000_000)
    assert set(VideoFrameSelector.model_fields) == {"kind", "timestamp_ns"}


@pytest.mark.parametrize(
    "media_type", ["image/jpeg", "application/x.nuscenes.lidar-pcd-bin"]
)
def test_a_selector_the_media_type_cannot_have_is_refused(media_type):
    with pytest.raises(ValidationError, match="cannot select part of"):
        PayloadRef.model_validate(
            _ref(
                media_type=media_type,
                selector={"kind": "video_frame", "timestamp_ns": 0},
            )
        )


@pytest.mark.parametrize(
    "selector",
    [
        {"kind": "byte_range", "timestamp_ns": 0},
        {"timestamp_ns": -1, "kind": "video_frame"},
        {"kind": "video_frame", "timestamp_ns": 0, "clock": "mcap_log_time"},
    ],
)
def test_unknown_selectors_and_canonical_clock_fields_are_refused(selector):
    with pytest.raises(ValidationError):
        PayloadRef.model_validate(
            _ref(uri=VIDEO_URI, media_type="video/mp4", selector=selector)
        )


@pytest.mark.parametrize(
    "field", ["artifact_id", "content_id", "source_kind", "relative_path", "kind"]
)
def test_no_source_or_storage_identity_can_be_carried(field):
    with pytest.raises(ValidationError, match="Extra inputs"):
        PayloadRef.model_validate(_ref(**{field: "x"}))


def test_the_json_round_trip_is_exact_and_deterministic():
    ref = PayloadRef.model_validate(
        _ref(
            uri=VIDEO_URI,
            media_type="video/mp4",
            selector={"kind": "video_frame", "timestamp_ns": 5},
        )
    )
    data = canonical_json_bytes(ref.model_dump(mode="json"))
    assert json.loads(data) == {
        "checksum": CHECKSUM,
        "media_type": "video/mp4",
        "selector": {"kind": "video_frame", "timestamp_ns": 5},
        "size_bytes": 15,
        "uri": VIDEO_URI,
    }
    assert PayloadRef.model_validate_json(data) == ref
    assert (
        canonical_json_bytes(
            PayloadRef.model_validate_json(data).model_dump(mode="json")
        )
        == data
    )


def test_it_is_immutable_and_hashable():
    ref = PayloadRef.model_validate(_ref())
    with pytest.raises(ValidationError):
        ref.size_bytes = 1  # type: ignore[misc]
    assert {ref: 1}[PayloadRef.model_validate(_ref())] == 1


def test_it_is_a_pinned_payload_for_the_verified_reader():
    ref = PayloadRef.model_validate(_ref(uri=FILE_URI, size_bytes=0))

    def read_contract(pin: PinnedPayload) -> tuple[str, str, int]:
        return pin.uri, pin.checksum, pin.size_bytes

    assert read_contract(ref) == (FILE_URI, CHECKSUM, 0)
    # The same bytes as the pin the locator issues for the whole object.
    pin = LogicalPayloadPin(
        uri=ref.uri,
        checksum=ref.checksum,
        size_bytes=ref.size_bytes,
        media_type=ref.media_type,
    )
    assert read_contract(pin) == read_contract(ref)


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "logical", **_ref()},
        {"kind": "artifact", **_ref()},
        {"artifact_id": "payload-0001", "checksum": CHECKSUM, "size_bytes": 15,
         "media_type": "image/jpeg"},
        {"kind": "source_file", "content_id": CONTENT_ID, "path": "samples/a.jpg",
         "sha256": CHECKSUM, "size_bytes": 1, "media_type": "image/jpeg"},
        {"uri": PAYLOAD_URI},
    ],
)  # fmt: skip
def test_a_reference_by_artifact_or_content_id_is_not_a_payload_ref(value):
    with pytest.raises(ValidationError):
        PayloadRef.model_validate(value)
