"""Contract tests for the source bindings, source revisions, pinned builder
inputs and source-time primitives (ADR-007 §27.2, §27.3; ADR-015 §4, §6)."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

import sceneops_core.provenance as provenance
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.provenance import (
    INT64_MAX,
    ContentSourceRevision,
    ContentUnitSource,
    PinnedContent,
    PinnedRecording,
    RecordingSegmentSource,
    RecordingSourceRevision,
    SourceRevision,
    SourceTimeUnit,
    promote_to_ns,
)
from sceneops_core.sources import (
    SourceContentInventory,
    SourceFileEntry,
    content_id_for,
)

CHECKSUM = "sha256:" + "a" * 64
CONTENT_CHECKSUM = "sha256:" + "c" * 64
CONTENT_ID = content_id_for(CONTENT_CHECKSUM)


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


# --- RecordingSegmentSource --------------------------------------------------


def test_recording_segment_source_round_trips_nanoseconds_exactly():
    source = _recording()
    payload = source.model_dump(mode="json")
    assert payload["start_timestamp_ns"] == 1_700_000_000_123_456_789
    assert payload["end_timestamp_ns"] == 1_700_000_010_123_456_790
    assert payload["source_kind"] == "recording"
    assert "uri" not in payload and "recording_uri" not in payload
    parsed = RecordingSegmentSource.model_validate_json(source.model_dump_json())
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


def test_source_revisions_are_a_closed_union_of_recording_and_content():
    """ADR-015 §4: two source kinds; nothing else is a canonical source."""
    adapter = TypeAdapter(SourceRevision)
    recording = adapter.validate_python(
        {
            "source_kind": "recording",
            "robot_run_id": "run-001",
            "recording_checksum": CHECKSUM,
        }
    )
    content = adapter.validate_python(
        {
            "source_kind": "content",
            "content_id": CONTENT_ID,
            "content_checksum": CONTENT_CHECKSUM,
        }
    )
    assert isinstance(recording, RecordingSourceRevision)
    assert isinstance(content, ContentSourceRevision)
    for kind in ("external", "simulation", "snapshot"):
        with pytest.raises(ValidationError):
            adapter.validate_python({"source_kind": kind, "content_id": CONTENT_ID})
    for removed in ("ExternalUnitSource", "ExternalSourceRevision", "UnitSourceKind"):
        assert not hasattr(provenance, removed)
    payload = _recording().model_dump(mode="json")
    for kind in ("external", "content"):
        with pytest.raises(ValidationError):
            RecordingSegmentSource.model_validate({**payload, "source_kind": kind})


# --- ContentUnitSource ---------------------------------------------------------


def _content(**overrides) -> ContentUnitSource:
    fields = {
        "content_id": CONTENT_ID,
        "content_checksum": CONTENT_CHECKSUM,
        "source_clock": "nuscenes.timestamp",
        "start_timestamp_ns": 1_532_402_927_604_844_000,
        "end_timestamp_ns": 1_532_402_947_198_048_001,
        "unit_key": "cc8c0bf57f984915a77078b10eb33198",
    }
    fields.update(overrides)
    return ContentUnitSource(**fields)


def test_content_unit_source_round_trips_and_carries_no_location_or_declaration():
    source = _content()
    payload = source.model_dump(mode="json")
    assert payload["source_kind"] == "content"
    assert set(payload) == {
        "source_kind",
        "content_id",
        "content_checksum",
        "source_clock",
        "start_timestamp_ns",
        "end_timestamp_ns",
        "unit_key",
    }
    assert ContentUnitSource.model_validate_json(source.model_dump_json()) == source


def test_content_unit_source_rejects_declaration_location_and_execution_fields():
    payload = _content().model_dump(mode="json")
    for extra in (
        "snapshot_id",
        "format",
        "upstream",
        "upstream_revision",
        "license",
        "uri",
        "robot_run_id",
        "dataset_version",
        "job_id",
    ):
        with pytest.raises(ValidationError):
            ContentUnitSource.model_validate({**payload, extra: "x"})


def test_content_unit_source_requires_the_content_id_of_its_checksum():
    other = content_id_for("sha256:" + "d" * 64)
    with pytest.raises(ValidationError, match="content_id"):
        _content(content_id=other)
    with pytest.raises(ValidationError, match="content_id"):
        _content(content_id="content-not-an-id")


def test_content_unit_source_allows_a_unit_relative_window_from_zero():
    source = _content(
        source_clock="lerobot.frame_time",
        start_timestamp_ns=0,
        end_timestamp_ns=100_000_001,
        unit_key="0",
    )
    assert source.contains(0)
    assert source.contains(100_000_000)
    assert not source.contains(100_000_001)


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_timestamp_ns": 5, "end_timestamp_ns": 5},
        {"start_timestamp_ns": -1, "end_timestamp_ns": 5},
        {"start_timestamp_ns": 1.0},
        {"source_clock": "NuScenes"},
        {"unit_key": " scene-0061"},
        {"unit_key": ""},
        {"content_checksum": "md5:abc"},
    ],
)
def test_content_unit_source_rejects_invalid_fields(overrides):
    with pytest.raises(ValidationError):
        _content(**overrides)


def test_content_source_revision_excludes_window_and_unit_key():
    revision = _content().source_revision()
    assert revision == ContentSourceRevision(
        content_id=CONTENT_ID, content_checksum=CONTENT_CHECKSUM
    )
    assert (
        _content(
            unit_key="other", start_timestamp_ns=1, end_timestamp_ns=2
        ).source_revision()
        == revision
    )
    with pytest.raises(ValidationError, match="content_id"):
        ContentSourceRevision(
            content_id=content_id_for("sha256:" + "d" * 64),
            content_checksum=CONTENT_CHECKSUM,
        )


# --- pinned builder inputs ------------------------------------------------------


def test_pinned_recording_issues_the_bindings_builders_used_to_assemble():
    pinned = PinnedRecording(
        robot_run_id="run-001",
        recording_checksum=CHECKSUM,
        recording_clock="mcap_log_time",
    )
    assert pinned.source_revision == RecordingSourceRevision(
        robot_run_id="run-001", recording_checksum=CHECKSUM
    )
    source = pinned.segment_source(
        source_clock="mcap_log_time",
        start_timestamp_ns=1_700_000_000_123_456_789,
        end_timestamp_ns=1_700_000_010_123_456_790,
        unit_key="segment-0000",
    )
    assert source == _recording()
    assert source.source_revision() == pinned.source_revision


@pytest.mark.parametrize(
    "overrides",
    [
        {"robot_run_id": "run/001"},
        {"recording_checksum": "sha256:" + "A" * 64},
        {"recording_clock": "MCAP"},
        {"dataset_version": "v1"},
    ],
)
def test_pinned_recording_rejects_invalid_fields(overrides):
    fields = {
        "robot_run_id": "run-001",
        "recording_checksum": CHECKSUM,
        "recording_clock": "mcap_log_time",
        **overrides,
    }
    with pytest.raises(ValidationError):
        PinnedRecording(**fields)


def test_pinned_content_binds_units_to_its_inventory():
    inventory = SourceContentInventory.create(
        [SourceFileEntry(path="v1.0-mini/scene.json", size_bytes=3, sha256=CHECKSUM)]
    )
    pinned = PinnedContent(inventory=inventory)
    assert pinned.content_id == inventory.content_id
    assert pinned.source_revision == ContentSourceRevision(
        content_id=inventory.content_id, content_checksum=inventory.content_checksum
    )
    source = pinned.unit_source(
        source_clock="nuscenes.timestamp",
        start_timestamp_ns=10,
        end_timestamp_ns=20,
        unit_key="cc8c0bf57f984915a77078b10eb33198",
    )
    assert source.content_id == inventory.content_id
    assert source.source_revision() == pinned.source_revision
    assert set(PinnedContent.model_fields) == {"inventory"}


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
