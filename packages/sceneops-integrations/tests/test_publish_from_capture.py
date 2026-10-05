"""Recoverable publication from a finalized capture (ADR-008 §4.2 A1 rule 2,
Phase 12.2): ``publish_from_capture`` / ``publish --from-capture``.

The capture directory is built the way Capture leaves it -- an MCAP plus a
canonical ``capture_receipt.json`` -- and published by fresh store/process
instances, so nothing but the directory carries the publication inputs.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sceneops_core.robots.capture_receipt import (
    CAPTURE_RECEIPT_FILENAME,
    CaptureReceipt,
    CaptureReceiptError,
    FinalizationReason,
    ReceiptFinalization,
    ReceiptKafka,
    ReceiptRecording,
)
from sceneops_core.robots.manifest import (
    CaptureInfo,
    CaptureSource,
    CaptureSourceKind,
    RecordingFormat,
    load_canonical_robot_run_manifest,
)
from sceneops_integrations.recording import (
    CaptureReceiptMismatchError,
    CaptureReceiptMissingError,
    RecordingPublicationConflictError,
    RecordingValidationError,
    derive_mcap_facts,
    publish_from_capture,
    publish_recording,
    sha256_checksum,
)
from sceneops_storage import LocalArtifactStore

# (topic, schema_name, log_time_ns) per message
DEFAULT_MESSAGES = [
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_000_000_001_999),
    ("/vehicle/imu", "sensor_msgs/msg/Imu", 1_700_000_000_500_000_000),
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_001_000_000_000),
    ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_002_000_000_999),
]


def build_mcap(messages) -> bytes:
    from mcap.writer import Writer

    buffer = io.BytesIO()
    writer = Writer(buffer)
    writer.start(profile="ros2", library="test")
    channels: dict[tuple[str, str], int] = {}
    for sequence, (topic, schema_name, log_time) in enumerate(messages):
        if (topic, schema_name) not in channels:
            schema_id = writer.register_schema(
                name=schema_name, encoding="ros2msg", data=b"# test"
            )
            channels[(topic, schema_name)] = writer.register_channel(
                topic=topic, message_encoding="cdr", schema_id=schema_id
            )
        writer.add_message(
            channels[(topic, schema_name)],
            log_time=log_time,
            data=b"\x00\x01",
            publish_time=log_time,
            sequence=sequence,
        )
    writer.finish()
    return buffer.getvalue()


_TOPIC = "sceneops.robot.telemetry.v1"
_SOURCE = CaptureSource(kind=CaptureSourceKind.KAFKA, topics=[_TOPIC])


class _SpyStore(LocalArtifactStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root_uri=str(root))
        self.writes: list[str] = []

    async def write_bytes(self, uri, data):
        self.writes.append(uri)
        await super().write_bytes(uri, data)


def _receipt_for(
    mcap_bytes: bytes,
    *,
    run_id: str = "run-001",
    robot_id: str = "robot-001",
    robot_platform: str | None = "nuscenes-can-replay",
    **overrides,
) -> CaptureReceipt:
    facts = derive_mcap_facts(io.BytesIO(mcap_bytes), source_clock="mcap_log_time")
    fields = dict(
        run_id=run_id,
        robot_id=robot_id,
        robot_platform=robot_platform,
        recording=ReceiptRecording(
            file=f"{run_id}_0.mcap",
            format=RecordingFormat.MCAP,
            checksum=sha256_checksum(mcap_bytes),
            size_bytes=len(mcap_bytes),
        ),
        capture=CaptureInfo(source=_SOURCE, source_clock="mcap_log_time"),
        message_count=facts.message_count,
        per_channel_counts={c.topic: c.message_count for c in facts.channels},
        finalization=ReceiptFinalization(
            reason=FinalizationReason.EXPLICIT_RUN_END,
            finalized_at=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
        ),
        kafka=ReceiptKafka(
            partition=0,
            first_offset=0,
            last_offset=facts.message_count,
            first_sequence=0,
            last_sequence=facts.message_count - 1,
        ),
    )
    fields.update(overrides)
    return CaptureReceipt(**fields)


def make_capture(
    base: Path,
    *,
    run_id: str = "run-001",
    messages=DEFAULT_MESSAGES,
    **receipt_overrides,
) -> Path:
    """A finalized capture directory: ``<base>/<run_id>/`` with the MCAP and
    its receipt."""
    mcap_bytes = build_mcap(messages)
    directory = base / run_id
    directory.mkdir(parents=True)
    (directory / f"{run_id}_0.mcap").write_bytes(mcap_bytes)
    receipt = _receipt_for(mcap_bytes, run_id=run_id, **receipt_overrides)
    (directory / CAPTURE_RECEIPT_FILENAME).write_bytes(receipt.to_canonical_bytes())
    return directory


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    path = tmp_path / "store"
    path.mkdir()
    return path


@pytest.fixture()
def capture_dir(tmp_path: Path) -> Path:
    return make_capture(tmp_path / "capture")


def _kwargs(root: Path, capture_dir: Path) -> dict:
    return dict(root_uri=str(root / "robot_runs"), capture_dir=capture_dir)


class TestPublishFromCapture:
    async def test_publishes_recording_and_manifest_from_receipt_alone(
        self, root, capture_dir
    ) -> None:
        store = _SpyStore(root)

        publication = await publish_from_capture(
            artifact_store=store, **_kwargs(root, capture_dir)
        )

        assert publication.recording_written and publication.manifest_written
        assert store.writes == [publication.recording_uri, publication.manifest_uri]
        manifest = load_canonical_robot_run_manifest(
            await store.read_bytes(publication.manifest_uri)
        )
        assert manifest.run_id == "run-001"
        assert manifest.robot_id == "robot-001"
        assert manifest.robot_platform == "nuscenes-can-replay"
        assert manifest.capture.source == _SOURCE
        assert manifest.capture.source_clock == "mcap_log_time"
        assert (
            await store.read_bytes(publication.recording_uri)
            == (capture_dir / "run-001_0.mcap").read_bytes()
        )

    async def test_manifest_is_byte_identical_to_explicit_input_publication(
        self, tmp_path, capture_dir
    ) -> None:
        """One implementation path: the receipt supplies exactly the inputs
        the explicit publisher takes, so both produce the same objects."""
        from_capture_root = tmp_path / "a"
        explicit_root = tmp_path / "b"
        from_capture_root.mkdir()
        explicit_root.mkdir()
        # Same root string in the manifest requires the same root URI.
        uri_root = str(tmp_path / "shared_root" / "robot_runs")

        a = await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(from_capture_root)),
            root_uri=uri_root,
            capture_dir=capture_dir,
        )
        a_manifest = Path(a.manifest_uri).read_bytes()
        a_recording = Path(a.recording_uri).read_bytes()
        import shutil

        shutil.rmtree(tmp_path / "shared_root")

        b = await publish_recording(
            artifact_store=LocalArtifactStore(root_uri=str(explicit_root)),
            root_uri=uri_root,
            recording_path=capture_dir / "run-001_0.mcap",
            run_id="run-001",
            robot_id="robot-001",
            robot_platform="nuscenes-can-replay",
            capture_source=_SOURCE,
            source_clock="mcap_log_time",
        )

        assert Path(b.manifest_uri).read_bytes() == a_manifest
        assert Path(b.recording_uri).read_bytes() == a_recording
        assert b.manifest_checksum == a.manifest_checksum

    async def test_facts_come_from_the_bytes_not_the_receipt(
        self, root, capture_dir
    ) -> None:
        publication = await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, capture_dir),
        )
        payload = json.loads(publication.manifest.to_canonical_bytes())
        # Same derivation as the explicit path: min/max log_time of the MCAP.
        assert payload["started_at"] == "2023-11-14T22:13:20.000001Z"
        assert payload["ended_at"] == "2023-11-14T22:13:22.000000Z"
        assert payload["recording"]["checksum"] == sha256_checksum(
            (capture_dir / "run-001_0.mcap").read_bytes()
        )
        # Nothing of the receipt's own metadata leaks into the manifest.
        assert "kafka" not in payload and "finalization" not in payload

    async def test_receipt_without_platform_publishes_null_platform(
        self, root, tmp_path
    ) -> None:
        directory = make_capture(tmp_path / "c", robot_platform=None)
        publication = await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, directory),
        )
        assert publication.manifest.robot_platform is None

    async def test_publication_never_writes_into_the_capture_directory(
        self, root, capture_dir
    ) -> None:
        before = {p.name: p.read_bytes() for p in capture_dir.iterdir()}
        await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, capture_dir),
        )
        assert {p.name: p.read_bytes() for p in capture_dir.iterdir()} == before

    async def test_receipt_is_not_published(self, root, capture_dir) -> None:
        await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, capture_dir),
        )
        published = sorted(p.name for p in (root / "robot_runs" / "run-001").iterdir())
        assert published == ["recording.mcap", "robot_run_manifest.json"]


class TestRetryAndConvergence:
    async def test_retry_converges_and_writes_nothing(self, root, capture_dir) -> None:
        first = await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, capture_dir),
        )
        manifest_bytes = Path(first.manifest_uri).read_bytes()
        recording_bytes = Path(first.recording_uri).read_bytes()

        spy = _SpyStore(root)
        second = await publish_from_capture(
            artifact_store=spy, **_kwargs(root, capture_dir)
        )

        assert spy.writes == []
        assert not second.recording_written and not second.manifest_written
        assert second.manifest_checksum == first.manifest_checksum
        assert Path(second.manifest_uri).read_bytes() == manifest_bytes
        assert Path(second.recording_uri).read_bytes() == recording_bytes

    async def test_retry_after_crash_before_manifest_reuses_recording(
        self, root, capture_dir
    ) -> None:
        class Crashing(_SpyStore):
            async def write_bytes(self, uri, data):
                if uri.endswith("robot_run_manifest.json"):
                    raise OSError("injected crash before manifest")
                await super().write_bytes(uri, data)

        with pytest.raises(OSError, match="injected"):
            await publish_from_capture(
                artifact_store=Crashing(root), **_kwargs(root, capture_dir)
            )
        assert not (
            root / "robot_runs" / "run-001" / "robot_run_manifest.json"
        ).exists()

        retry = _SpyStore(root)
        publication = await publish_from_capture(
            artifact_store=retry, **_kwargs(root, capture_dir)
        )
        assert not publication.recording_written and publication.manifest_written
        assert retry.writes == [publication.manifest_uri]

    async def test_killed_local_write_does_not_become_a_conflicting_object(
        self, root, capture_dir
    ) -> None:
        """A publisher killed mid-upload leaves, at most, a temp file next to
        the key (atomic LocalArtifactStore). The truncated bytes are never at
        the final key, so the retry publishes instead of reporting a
        permanent write-once conflict."""
        directory = root / "robot_runs" / "run-001"
        directory.mkdir(parents=True)
        (directory / ".recording.mcap.deadbeef.tmp").write_bytes(b"truncated")

        publication = await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, capture_dir),
        )

        assert publication.recording_written and publication.manifest_written
        assert (
            Path(publication.recording_uri).read_bytes()
            == (capture_dir / "run-001_0.mcap").read_bytes()
        )

    async def test_different_run_metadata_in_the_receipt_conflicts_with_manifest(
        self, root, tmp_path
    ) -> None:
        first = make_capture(tmp_path / "one")
        await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, first),
        )
        manifest_path = root / "robot_runs" / "run-001" / "robot_run_manifest.json"
        before = manifest_path.read_bytes()

        # Same run id and identical recording bytes, different robot identity.
        other = make_capture(tmp_path / "two", robot_id="robot-other")
        with pytest.raises(RecordingPublicationConflictError):
            await publish_from_capture(
                artifact_store=LocalArtifactStore(root_uri=str(root)),
                **_kwargs(root, other),
            )
        assert manifest_path.read_bytes() == before

    async def test_different_recording_for_a_published_run_conflicts(
        self, root, tmp_path
    ) -> None:
        first = make_capture(tmp_path / "one")
        await publish_from_capture(
            artifact_store=LocalArtifactStore(root_uri=str(root)),
            **_kwargs(root, first),
        )
        recording_path = root / "robot_runs" / "run-001" / "recording.mcap"
        before = recording_path.read_bytes()

        changed = make_capture(
            tmp_path / "two",
            messages=[
                ("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_000_000_000_000)
            ],
        )
        with pytest.raises(RecordingPublicationConflictError):
            await publish_from_capture(
                artifact_store=LocalArtifactStore(root_uri=str(root)),
                **_kwargs(root, changed),
            )
        assert recording_path.read_bytes() == before
        assert (root / "robot_runs" / "run-001" / "robot_run_manifest.json").exists()


class TestRestartRecovery:
    """A fresh process with nothing but the finalized capture directory."""

    def test_fresh_process_publishes_from_capture_only(self, root, capture_dir) -> None:
        env = {
            **os.environ,
            "SCENEOPS_PUBLISHER_ARTIFACT__BACKEND": "local",
            "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI": str(root),
        }
        command = [
            sys.executable,
            "-m",
            "sceneops_integrations.recording",
            "publish",
            "--from-capture",
            str(capture_dir),
        ]

        first = subprocess.run(command, capture_output=True, text=True, env=env)
        assert first.returncode == 0, first.stderr
        first_result = json.loads(first.stdout)
        assert first_result["recording_written"] and first_result["manifest_written"]

        # A second, independent process: converges to the same objects.
        second = subprocess.run(command, capture_output=True, text=True, env=env)
        assert second.returncode == 0, second.stderr
        second_result = json.loads(second.stdout)
        assert not second_result["recording_written"]
        assert not second_result["manifest_written"]
        assert second_result["manifest_checksum"] == first_result["manifest_checksum"]
        manifest = load_canonical_robot_run_manifest(
            Path(second_result["manifest_uri"]).read_bytes()
        )
        assert manifest.robot_id == "robot-001"


class TestReceiptValidation:
    async def _publish(self, root, directory):
        store = _SpyStore(root)
        try:
            return await publish_from_capture(
                artifact_store=store, **_kwargs(root, directory)
            )
        finally:
            # Callers assert that a rejection happened before any write.
            self.writes = store.writes

    async def test_missing_receipt(self, root, capture_dir) -> None:
        (capture_dir / CAPTURE_RECEIPT_FILENAME).unlink()
        with pytest.raises(CaptureReceiptMissingError):
            await self._publish(root, capture_dir)
        assert self.writes == []

    @pytest.mark.parametrize(
        "corrupt",
        [
            lambda b: b[: len(b) // 2],
            lambda b: b"",
            lambda b: b"garbage",
            lambda b: b + b"\n",
            lambda b: b.replace(b'"robot_id":"robot-001"', b'"robot_id":"robot-002"x'),
        ],
        ids=["truncated", "empty", "garbage", "non-canonical", "invalid"],
    )
    async def test_corrupt_receipt_is_rejected_not_treated_as_absent(
        self, root, capture_dir, corrupt
    ) -> None:
        path = capture_dir / CAPTURE_RECEIPT_FILENAME
        path.write_bytes(corrupt(path.read_bytes()))
        with pytest.raises(CaptureReceiptError):
            await self._publish(root, capture_dir)
        assert self.writes == []

    async def test_receipt_for_another_run_directory_is_rejected(
        self, root, tmp_path
    ) -> None:
        directory = make_capture(tmp_path / "c", run_id="run-001")
        renamed = directory.rename(directory.with_name("run-002"))
        with pytest.raises(CaptureReceiptMismatchError, match="run_id"):
            await self._publish(root, renamed)
        assert self.writes == []

    async def test_tampered_recording_bytes_are_rejected(
        self, root, capture_dir
    ) -> None:
        mcap = capture_dir / "run-001_0.mcap"
        data = bytearray(mcap.read_bytes())
        data[len(data) // 2] ^= 0xFF
        mcap.write_bytes(bytes(data))
        with pytest.raises(CaptureReceiptMismatchError, match="checksum"):
            await self._publish(root, capture_dir)
        assert self.writes == []

    async def test_truncated_recording_is_rejected(self, root, capture_dir) -> None:
        mcap = capture_dir / "run-001_0.mcap"
        mcap.write_bytes(mcap.read_bytes()[:-5])
        with pytest.raises(CaptureReceiptMismatchError, match="size_bytes"):
            await self._publish(root, capture_dir)
        assert self.writes == []

    async def test_missing_recording_is_rejected(self, root, capture_dir) -> None:
        (capture_dir / "run-001_0.mcap").unlink()
        with pytest.raises(RecordingValidationError, match="not found"):
            await self._publish(root, capture_dir)
        assert self.writes == []

    @pytest.mark.parametrize(
        "overrides, match",
        [
            (
                {
                    "message_count": 99,
                    "per_channel_counts": {"/vehicle/imu": 1, "/vehicle/odom": 98},
                },
                "per_channel_counts|message_count",
            ),
            (
                {"per_channel_counts": {"/vehicle/imu": 2, "/vehicle/odom": 2}},
                "per_channel_counts",
            ),
        ],
        ids=["message_count", "channel_counts"],
    )
    async def test_receipt_claims_that_disagree_with_the_bytes_are_rejected(
        self, root, tmp_path, overrides, match
    ) -> None:
        # The recording bytes are valid and match checksum/size: only the
        # receipt's claims about the content are wrong.
        directory = make_capture(tmp_path / "c", **overrides)
        with pytest.raises(CaptureReceiptMismatchError, match=match):
            await self._publish(root, directory)
        assert self.writes == []

    async def test_partial_capture_directory_is_rejected(self, root, tmp_path) -> None:
        directory = make_capture(tmp_path / ".partial")
        with pytest.raises(RecordingValidationError, match="partial"):
            await self._publish(root, directory)
        assert self.writes == []

    async def test_missing_directory_is_rejected(self, root, tmp_path) -> None:
        with pytest.raises(RecordingValidationError, match="not found"):
            await self._publish(root, tmp_path / "nope")

    async def test_receipt_does_not_override_recording_derived_facts(
        self, root, tmp_path
    ) -> None:
        """A receipt cannot manufacture a recording fact: its claim that the
        recording is a different file is checked, never trusted."""
        directory = make_capture(tmp_path / "c")
        other = build_mcap(
            [("/vehicle/odom", "nav_msgs/msg/Odometry", 1_700_000_000_000_000_000)]
        )
        (directory / "run-001_0.mcap").write_bytes(other)
        # Receipt still describes the original bytes.
        assert (
            hashlib.sha256(other).hexdigest()
            not in (directory / CAPTURE_RECEIPT_FILENAME).read_text()
        )
        with pytest.raises(CaptureReceiptMismatchError):
            await self._publish(root, directory)
        assert self.writes == []


class TestCliInputs:
    def _env(self, monkeypatch, root):
        monkeypatch.setenv("SCENEOPS_PUBLISHER_ARTIFACT__BACKEND", "local")
        monkeypatch.setenv("SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI", str(root))

    def test_from_capture_prints_publication_result(
        self, root, capture_dir, monkeypatch, capsys
    ) -> None:
        from sceneops_integrations.recording.cli import main

        self._env(monkeypatch, root)
        assert main(["publish", "--from-capture", str(capture_dir)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["run_id"] == "run-001"
        assert result["manifest_uri"] == str(
            root / "robot_runs" / "run-001" / "robot_run_manifest.json"
        )

    @pytest.mark.parametrize(
        "extra",
        [
            ["--run-id", "other"],
            ["--robot-id", "other"],
            ["--robot-platform", "other"],
            ["--source-kind", "file"],
            ["--source-topic", "t"],
            ["--source-clock", "mcap_log_time"],
            ["--mcap-path", "x.mcap"],
        ],
    )
    def test_from_capture_rejects_explicit_inputs(
        self, capture_dir, extra, capsys
    ) -> None:
        from sceneops_integrations.recording.cli import main

        with pytest.raises(SystemExit) as exit_info:
            main(["publish", "--from-capture", str(capture_dir), *extra])
        assert exit_info.value.code == 2
        assert "cannot be combined" in capsys.readouterr().err

    def test_publish_without_any_inputs_is_rejected(self, capsys) -> None:
        from sceneops_integrations.recording.cli import main

        with pytest.raises(SystemExit) as exit_info:
            main(["publish"])
        assert exit_info.value.code == 2
        assert "--from-capture" in capsys.readouterr().err

    def test_partial_explicit_inputs_are_still_rejected(self, capsys) -> None:
        from sceneops_integrations.recording.cli import main

        with pytest.raises(SystemExit):
            main(["publish", "--mcap-path", "x.mcap", "--run-id", "r"])
        assert "missing" in capsys.readouterr().err

    def test_failure_exits_non_zero(self, root, tmp_path, monkeypatch, capsys) -> None:
        from sceneops_integrations.recording.cli import main

        self._env(monkeypatch, root)
        assert main(["publish", "--from-capture", str(tmp_path / "nope")]) == 1
        assert "publish failed" in capsys.readouterr().err
