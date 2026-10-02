"""Contract tests for the source provenance blocks and source-time
primitives (ADR-007 §27.2, §27.3)."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.datasets import ExternalDatasetRef
from sceneops_core.provenance import (
    INT64_MAX,
    ExternalSourceRevision,
    ExternalUnitSource,
    RecordingSegmentSource,
    RecordingSourceRevision,
    SourceTimeUnit,
    UnitSource,
    promote_to_ns,
)

CHECKSUM = "sha256:" + "a" * 64
UNIT_SOURCE = TypeAdapter(UnitSource)


def _external(**overrides) -> ExternalUnitSource:
    fields = {
        "external_ref": ExternalDatasetRef(
            format="nuscenes",
            format_version="v1.0-mini",
            uri="/data/raw/nuscenes",
            external_revision="r1",
        ),
        "source_unit_key": "cc8c0bf57f984915a77078b10eb33198",
    }
    fields.update(overrides)
    return ExternalUnitSource(**fields)


def _recording(**overrides) -> RecordingSegmentSource:
    fields = {
        "robot_run_id": "run-001",
        "recording_artifact_id": robot_run_recording_artifact_id("run-001"),
        "recording_checksum": CHECKSUM,
        "source_clock": "mcap_log_time",
        "start_timestamp_ns": 1_700_000_000_123_456_789,
        "end_timestamp_ns": 1_700_000_010_123_456_790,
        "unit_key": "segment-0000",
    }
    fields.update(overrides)
    return RecordingSegmentSource(**fields)


# --- ExternalUnitSource ------------------------------------------------------


def test_external_unit_source_round_trips_through_json():
    source = _external()
    payload = source.model_dump(mode="json")
    assert payload == {
        "source_kind": "external",
        "external_ref": {
            "format": "nuscenes",
            "format_version": "v1.0-mini",
            "uri": "/data/raw/nuscenes",
            "external_name": None,
            "external_revision": "r1",
            "checksum": None,
        },
        "source_unit_key": "cc8c0bf57f984915a77078b10eb33198",
    }
    assert UNIT_SOURCE.validate_python(payload) == source


def test_external_unit_source_rejects_unknown_fields():
    payload = _external().model_dump(mode="json")
    with pytest.raises(ValidationError):
        ExternalUnitSource.model_validate({**payload, "scene_token": "x"})
    payload["external_ref"]["root_path"] = "/data"
    with pytest.raises(ValidationError):
        ExternalUnitSource.model_validate(payload)


@pytest.mark.parametrize("key", ["", " tok", "tok ", "a\nb", "x" * 257])
def test_external_unit_source_rejects_ambiguous_unit_keys(key):
    with pytest.raises(ValidationError):
        _external(source_unit_key=key)


def test_external_unit_source_is_immutable():
    source = _external()
    with pytest.raises(ValidationError):
        source.source_unit_key = "other"


def test_external_source_revision_excludes_location_and_unit_key():
    revision = _external().source_revision()
    assert revision == ExternalSourceRevision(
        format="nuscenes",
        format_version="v1.0-mini",
        external_revision="r1",
        checksum=None,
    )
    moved = _external(
        external_ref=ExternalDatasetRef(
            format="nuscenes",
            format_version="v1.0-mini",
            uri="s3://elsewhere/nuscenes",
            external_name="renamed",
            external_revision="r1",
        ),
        source_unit_key="another-scene",
    )
    assert moved.source_revision() == revision


# --- RecordingSegmentSource --------------------------------------------------


def test_recording_segment_source_round_trips_nanoseconds_exactly():
    source = _recording()
    payload = source.model_dump(mode="json")
    assert payload["start_timestamp_ns"] == 1_700_000_000_123_456_789
    assert payload["end_timestamp_ns"] == 1_700_000_010_123_456_790
    assert payload["source_kind"] == "recording"
    assert "uri" not in payload and "recording_uri" not in payload
    parsed = UNIT_SOURCE.validate_json(source.model_dump_json())
    assert parsed == source
    assert parsed.start_timestamp_ns == 1_700_000_000_123_456_789


def test_recording_segment_source_window_is_half_open():
    source = _recording(start_timestamp_ns=100, end_timestamp_ns=200)
    assert source.contains(100)
    assert source.contains(199)
    assert not source.contains(200)
    assert not source.contains(99)


@pytest.mark.parametrize(
    ("start", "end"), [(200, 200), (201, 200), (-1, 10), (0, INT64_MAX + 1)]
)
def test_recording_segment_source_rejects_invalid_windows(start, end):
    with pytest.raises(ValidationError):
        _recording(start_timestamp_ns=start, end_timestamp_ns=end)


@pytest.mark.parametrize("value", [1.5e18, 1_700_000_000.0, "1700000000", True])
def test_recording_segment_source_rejects_non_integer_timestamps(value):
    with pytest.raises(ValidationError):
        _recording(start_timestamp_ns=value)


def test_recording_segment_source_rejects_unknown_fields():
    payload = _recording().model_dump(mode="json")
    for extra in ("job_id", "dataset_version", "scene_id", "mcap_uri"):
        with pytest.raises(ValidationError):
            RecordingSegmentSource.model_validate({**payload, extra: "x"})


def test_recording_segment_source_requires_the_runs_recording_artifact():
    with pytest.raises(ValidationError, match="recording_artifact_id"):
        _recording(recording_artifact_id=robot_run_recording_artifact_id("run-002"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"recording_checksum": "sha256:" + "A" * 64},
        {"recording_checksum": "md5:abc"},
        {"source_clock": "MCAP_LOG_TIME"},
        {"source_clock": ""},
        {"robot_run_id": "run/001"},
    ],
)
def test_recording_segment_source_rejects_invalid_identity_fields(overrides):
    with pytest.raises(ValidationError):
        _recording(**overrides)


def test_recording_segment_source_accepts_dataset_qualified_clock():
    assert _recording(source_clock="nuscenes.timestamp").source_clock == (
        "nuscenes.timestamp"
    )


def test_recording_source_revision_excludes_window_and_unit_key():
    revision = _recording().source_revision()
    assert revision == RecordingSourceRevision(
        robot_run_id="run-001", recording_checksum=CHECKSUM
    )
    other_segment = _recording(
        start_timestamp_ns=5, end_timestamp_ns=6, unit_key="segment-0001"
    )
    assert other_segment.source_revision() == revision


def test_unit_source_union_dispatches_on_source_kind():
    assert isinstance(
        UNIT_SOURCE.validate_python(_external().model_dump(mode="json")),
        ExternalUnitSource,
    )
    assert isinstance(
        UNIT_SOURCE.validate_python(_recording().model_dump(mode="json")),
        RecordingSegmentSource,
    )
    payload = _recording().model_dump(mode="json")
    payload["source_kind"] = "simulation"
    with pytest.raises(ValidationError):
        UNIT_SOURCE.validate_python(payload)


# --- source-time promotion ---------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (1_532_402_927_647_951, SourceTimeUnit.MICROSECONDS, 1_532_402_927_647_951_000),
        (1_532_402_927_647, SourceTimeUnit.MILLISECONDS, 1_532_402_927_647_000_000),
        (1_532_402_927, SourceTimeUnit.SECONDS, 1_532_402_927_000_000_000),
        (
            1_700_000_000_123_456_789,
            SourceTimeUnit.NANOSECONDS,
            1_700_000_000_123_456_789,
        ),
        (0, "us", 0),
    ],
)
def test_promote_to_ns_is_exact(value, unit, expected):
    assert promote_to_ns(value, unit) == expected


@pytest.mark.parametrize("value", [1.5, 1_532_402_927.647951, True, "10"])
def test_promote_to_ns_rejects_non_integers(value):
    with pytest.raises(TypeError):
        promote_to_ns(value, SourceTimeUnit.MICROSECONDS)


@pytest.mark.parametrize(
    ("value", "unit"),
    [(-1, SourceTimeUnit.NANOSECONDS), (2**63, SourceTimeUnit.SECONDS)],
)
def test_promote_to_ns_rejects_out_of_range(value, unit):
    with pytest.raises(ValueError):
        promote_to_ns(value, unit)
