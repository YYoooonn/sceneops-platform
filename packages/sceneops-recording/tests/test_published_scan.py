"""Read-only scan and classification of published RobotRun objects (ADR-008
§5.1, Phase 12.3): the manifest is the publication marker (L-5), and the scan
never writes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sceneops_core.artifacts.contracts import ArtifactObject
from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.robots.manifest import (
    CaptureInfo,
    CaptureSource,
    CaptureSourceKind,
    ChannelFact,
    RecordingFormat,
    RecordingRef,
    RobotRunManifest,
)
from sceneops_recording.published_scan import (
    MANIFEST_OBJECT_NAME,
    RECORDING_OBJECT_NAME,
    PublicationClass,
    RecordingByteCheck,
    classify_publication,
    observe_published_runs,
    verify_recording_bytes,
)

ROOT = "s3://bucket/robot_runs"
_MTIME = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
RECORDING = b"mcap-bytes-" * 10


class MemoryStore:
    """The ArtifactStore surface the scan uses, over a dict. Any write is a
    test failure: a scan is read-only."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.reads: list[str] = []

    def put(self, uri: str, data: bytes) -> None:
        self.objects[uri] = data

    async def list_objects(self, uri: str) -> list[ArtifactObject]:
        prefix = uri.rstrip("/") + "/"
        return sorted(
            (
                ArtifactObject(uri=key, size_bytes=len(data), last_modified=_MTIME)
                for key, data in self.objects.items()
                if key.startswith(prefix)
            ),
            key=lambda item: item.uri,
        )

    async def read_bytes(self, uri: str) -> bytes:
        self.reads.append(uri)
        return self.objects[uri]

    async def write_bytes(self, uri: str, data: bytes) -> None:
        raise AssertionError(f"scan wrote {uri}")

    async def write_json(self, uri: str, payload) -> None:
        raise AssertionError(f"scan wrote {uri}")

    async def delete_prefix(self, uri: str) -> None:
        raise AssertionError(f"scan deleted {uri}")


def _manifest(
    run_id: str, recording: bytes = RECORDING, **overrides
) -> RobotRunManifest:
    values = dict(
        run_id=run_id,
        robot_id="robot-001",
        robot_platform=None,
        started_at=datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 5, 11, 1, tzinfo=UTC),
        recording=RecordingRef(
            format=RecordingFormat.MCAP,
            uri=f"{ROOT}/{run_id}/{RECORDING_OBJECT_NAME}",
            checksum=sha256_checksum(recording),
            size_bytes=len(recording),
        ),
        capture=CaptureInfo(
            source=CaptureSource(kind=CaptureSourceKind.FILE),
            source_clock="mcap_log_time",
        ),
        channels=[
            ChannelFact(
                topic="/odom",
                message_encoding="cdr",
                schema_name="nav_msgs/msg/Odometry",
                schema_encoding="ros2msg",
                message_count=3,
            )
        ],
    )
    values.update(overrides)
    return RobotRunManifest(**values)


def _publish(store: MemoryStore, run_id: str, **manifest_overrides) -> bytes:
    manifest_bytes = _manifest(run_id, **manifest_overrides).to_canonical_bytes()
    store.put(f"{ROOT}/{run_id}/{RECORDING_OBJECT_NAME}", RECORDING)
    store.put(f"{ROOT}/{run_id}/{MANIFEST_OBJECT_NAME}", manifest_bytes)
    return manifest_bytes


async def _only_run(store: MemoryStore):
    scan = await observe_published_runs(store, ROOT)
    assert len(scan.runs) == 1
    return scan.runs[0]


async def test_complete_publication_is_published_and_exposes_manifest_facts():
    store = MemoryStore()
    manifest_bytes = _publish(store, "run-1")

    run = await _only_run(store)
    assessment = classify_publication(run)

    assert assessment.classification == PublicationClass.PUBLISHED
    assert assessment.reasons == ()
    assert run.manifest_checksum == sha256_checksum(manifest_bytes)
    assert run.manifest.robot_id == "robot-001"
    assert run.manifest.recording_size_bytes == len(RECORDING)
    assert run.recording.size_bytes == len(RECORDING)
    assert run.recording.last_modified == _MTIME
    assert run.recording_check == RecordingByteCheck.NOT_CHECKED


async def test_recording_without_manifest_is_not_published():
    store = MemoryStore()
    store.put(f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}", RECORDING)

    run = await _only_run(store)

    assert classify_publication(run).classification == (
        PublicationClass.RECORDING_WITHOUT_MANIFEST
    )
    assert run.manifest_object is None and run.manifest is None
    # The marker is never inferred from the recording, and no recording byte
    # is read by a scan.
    assert store.reads == []


async def test_valid_manifest_without_recording():
    store = MemoryStore()
    _publish(store, "run-1")
    del store.objects[f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}"]

    run = await _only_run(store)

    assert classify_publication(run).classification == (
        PublicationClass.MANIFEST_WITHOUT_RECORDING
    )
    assert run.manifest is not None and run.recording is None


@pytest.mark.parametrize(
    "bad_manifest",
    [
        b"not json at all",
        b"[1, 2]",
        b'{"schema_version": "sceneops.robot_run_manifest/v9"}',
        b'{"schema_version": "sceneops.robot_run_manifest/v1", "run_id": "x"}',
        b"",
    ],
    ids=["not_json", "not_object", "unknown_version", "missing_fields", "empty"],
)
async def test_malformed_manifest_is_not_a_marker(bad_manifest):
    store = MemoryStore()
    store.put(f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}", RECORDING)
    store.put(f"{ROOT}/run-1/{MANIFEST_OBJECT_NAME}", bad_manifest)

    run = await _only_run(store)
    assessment = classify_publication(run)

    assert assessment.classification == PublicationClass.MANIFEST_MALFORMED
    assert run.manifest is None and run.manifest_checksum is None
    assert run.manifest_error and "Error" in run.manifest_error
    assert assessment.reasons == (run.manifest_error,)


async def test_non_canonical_manifest_bytes_are_malformed():
    store = MemoryStore()
    store.put(f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}", RECORDING)
    canonical = _manifest("run-1").to_canonical_bytes()
    # Same content, different bytes: registration (R3) would refuse it.
    store.put(f"{ROOT}/run-1/{MANIFEST_OBJECT_NAME}", canonical + b"\n")

    run = await _only_run(store)

    assert classify_publication(run).classification == (
        PublicationClass.MANIFEST_MALFORMED
    )
    assert "NonCanonicalRobotRunManifestError" in run.manifest_error


async def test_listing_size_disagreeing_with_manifest_is_an_integrity_conflict():
    store = MemoryStore()
    _publish(store, "run-1")
    store.put(f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}", RECORDING + b"extra")

    assessment = classify_publication(await _only_run(store))

    assert assessment.classification == PublicationClass.INTEGRITY_CONFLICT
    assert assessment.reasons == ("recording_size_mismatch",)


async def test_same_size_different_bytes_is_found_by_the_byte_check_only():
    store = MemoryStore()
    _publish(store, "run-1")
    tampered = b"X" * len(RECORDING)
    store.put(f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}", tampered)

    run = await _only_run(store)
    # The listing cannot see it...
    assert classify_publication(run).classification == PublicationClass.PUBLISHED

    checked = await verify_recording_bytes(store, run)
    assessment = classify_publication(checked)

    assert checked.recording_check == RecordingByteCheck.CHECKSUM_MISMATCH
    assert checked.recording_actual_checksum == sha256_checksum(tampered)
    assert assessment.classification == PublicationClass.INTEGRITY_CONFLICT
    assert assessment.reasons == ("recording_checksum_mismatch",)


async def test_byte_check_match_keeps_published():
    store = MemoryStore()
    _publish(store, "run-1")

    checked = await verify_recording_bytes(store, await _only_run(store))

    assert checked.recording_check == RecordingByteCheck.MATCHES
    assert classify_publication(checked).classification == PublicationClass.PUBLISHED


async def test_byte_check_is_skipped_without_a_valid_manifest_or_recording():
    store = MemoryStore()
    store.put(f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}", RECORDING)

    run = await _only_run(store)

    assert await verify_recording_bytes(store, run) == run
    assert store.reads == []


async def test_manifest_naming_another_run_or_recording_location_conflicts():
    store = MemoryStore()
    # run-1's prefix holds run-2's manifest.
    store.put(f"{ROOT}/run-1/{RECORDING_OBJECT_NAME}", RECORDING)
    store.put(
        f"{ROOT}/run-1/{MANIFEST_OBJECT_NAME}",
        _manifest("run-2").to_canonical_bytes(),
    )

    assessment = classify_publication(await _only_run(store))

    assert assessment.classification == PublicationClass.INTEGRITY_CONFLICT
    assert assessment.reasons == (
        "manifest_recording_uri_mismatch",
        "manifest_run_id_mismatch",
    )


async def test_unexpected_and_stray_objects_are_reported_not_classified():
    store = MemoryStore()
    _publish(store, "run-1")
    store.put(f"{ROOT}/run-1/notes.txt", b"hi")
    store.put(f"{ROOT}/run-1/sub/deeper.bin", b"hi")
    store.put(f"{ROOT}/loose.json", b"{}")
    store.put(f"{ROOT}/lonely/readme.md", b"x")

    scan = await observe_published_runs(store, ROOT)

    assert [run.run_id for run in scan.runs] == ["run-1"]
    assert [o.uri.rsplit("/", 2)[-2:] for o in scan.runs[0].unexpected_objects] == [
        ["run-1", "notes.txt"],
        ["sub", "deeper.bin"],
    ]
    assert [(u.item.uri, u.reason) for u in scan.unrecognized] == [
        (f"{ROOT}/lonely/readme.md", "no_recording_or_manifest"),
        (f"{ROOT}/loose.json", "not_under_run_prefix"),
    ]
    assert classify_publication(scan.runs[0]).classification == (
        PublicationClass.PUBLISHED
    )


async def test_empty_root_scans_to_nothing():
    scan = await observe_published_runs(MemoryStore(), ROOT)

    assert scan.runs == () and scan.unrecognized == ()


async def test_runs_are_ordered_by_run_id_and_scanning_is_deterministic_and_pure():
    store = MemoryStore()
    for run_id in ("run-b", "run-a", "run-c"):
        _publish(store, run_id)
    before = dict(store.objects)

    first = await observe_published_runs(store, ROOT)
    second = await observe_published_runs(store, ROOT)

    assert [run.run_id for run in first.runs] == ["run-a", "run-b", "run-c"]
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert store.objects == before  # MemoryStore raises on any write
    # Only manifests were read: one per run, never a recording.
    assert all(uri.endswith(MANIFEST_OBJECT_NAME) for uri in store.reads)
    assert len(store.reads) == 6
