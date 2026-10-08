"""Database-free Recording Publisher (ADR-007 §7.2): P1-P5 ordering,
write-once idempotency/conflict, manifest-last publication marker, and the
layer-A import boundary.

Uses a real LocalArtifactStore under tmp_path; the real-MinIO behavior of
the same code path is covered by apps/worker/tests/robots/
test_registration_integration.py.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import sceneops_recording as recording_pkg
from sceneops_core.robots.manifest import (
    CaptureSource,
    CaptureSourceKind,
    load_canonical_robot_run_manifest,
)
from sceneops_recording import (
    RecordingPublicationConflictError,
    RecordingPublicationIntegrityError,
    RecordingValidationError,
    publish_recording,
    sha256_checksum,
)
from sceneops_storage import LocalArtifactStore

_KAFKA = CaptureSource(
    kind=CaptureSourceKind.KAFKA, topics=["sceneops.robot.telemetry.v1"]
)


class _SpyStore(LocalArtifactStore):
    """LocalArtifactStore that records every write, optionally corrupting
    or failing writes to URIs ending with a given suffix."""

    def __init__(self, root: Path, *, fail_suffix=None, corrupt_suffix=None):
        super().__init__(root_uri=str(root))
        self.writes: list[str] = []
        self._fail_suffix = fail_suffix
        self._corrupt_suffix = corrupt_suffix

    async def write_bytes(self, uri, data):
        if self._fail_suffix and uri.endswith(self._fail_suffix):
            raise OSError(f"injected write failure for {uri}")
        self.writes.append(uri)
        if self._corrupt_suffix and uri.endswith(self._corrupt_suffix):
            data = data + b"corruption"
        await super().write_bytes(uri, data)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    path = tmp_path / "store"
    path.mkdir()
    return path


async def _publish(store, root: Path, mcap: Path, **overrides):
    kwargs = dict(
        artifact_store=store,
        root_uri=str(root / "robot_runs"),
        recording_path=mcap,
        run_id="run-001",
        robot_id="robot-001",
        robot_platform="nuscenes-can-replay",
        capture_source=_KAFKA,
        source_clock="mcap_log_time",
    )
    kwargs.update(overrides)
    return await publish_recording(**kwargs)


class TestFreshPublish:
    async def test_publishes_recording_then_manifest(self, root, write_mcap) -> None:
        store = _SpyStore(root)
        mcap = write_mcap()

        publication = await _publish(store, root, mcap)

        assert publication.recording_written and publication.manifest_written
        assert publication.recording_uri.endswith("robot_runs/run-001/recording.mcap")
        assert publication.manifest_uri.endswith(
            "robot_runs/run-001/robot_run_manifest.json"
        )
        # Manifest is the publication marker: written strictly last.
        assert store.writes == [publication.recording_uri, publication.manifest_uri]

        stored_recording = await store.read_bytes(publication.recording_uri)
        assert stored_recording == mcap.read_bytes()

        manifest_bytes = await store.read_bytes(publication.manifest_uri)
        manifest = load_canonical_robot_run_manifest(manifest_bytes)
        assert manifest == publication.manifest
        assert manifest.recording.size_bytes == len(stored_recording)
        assert manifest.recording.uri == publication.recording_uri

    async def test_derives_facts_from_recording_bytes(self, root, write_mcap) -> None:
        publication = await _publish(
            LocalArtifactStore(root_uri=str(root)), root, write_mcap()
        )
        payload = json.loads(publication.manifest.to_canonical_bytes())

        # min/max message log_time, truncated (floor) to microseconds.
        assert payload["started_at"] == "2023-11-14T22:13:20.000001Z"
        assert payload["ended_at"] == "2023-11-14T22:13:22.000000Z"
        assert payload["channels"] == [
            {
                "message_count": 1,
                "message_encoding": "cdr",
                "schema_encoding": "ros2msg",
                "schema_name": "sensor_msgs/msg/Imu",
                "topic": "/vehicle/imu",
            },
            {
                "message_count": 3,
                "message_encoding": "cdr",
                "schema_encoding": "ros2msg",
                "schema_name": "nav_msgs/msg/Odometry",
                "topic": "/vehicle/odom",
            },
        ]
        assert publication.manifest.started_at == datetime(
            2023, 11, 14, 22, 13, 20, 1, tzinfo=UTC
        )

    async def test_identical_inputs_produce_identical_manifest_bytes(
        self, tmp_path, write_mcap
    ) -> None:
        mcap = write_mcap()
        results = []
        for name in ("a", "b"):
            # Same root URI string, two independent stores/processes.
            store_root = tmp_path / name
            store_root.mkdir()
            store = LocalArtifactStore(root_uri=str(store_root))
            publication = await _publish(
                store, tmp_path, mcap, root_uri=str(store_root / "robot_runs")
            )
            results.append(publication)
        # Different storage roots are different publication inputs.
        assert results[0].manifest_checksum != results[1].manifest_checksum

        store = LocalArtifactStore(root_uri=str(tmp_path / "a"))
        again = await _publish(
            store, tmp_path, mcap, root_uri=str(tmp_path / "a" / "robot_runs")
        )
        assert (
            again.manifest.to_canonical_bytes()
            == results[0].manifest.to_canonical_bytes()
        )


class TestRetryAndConflict:
    async def test_identical_retry_writes_nothing(self, root, write_mcap) -> None:
        mcap = write_mcap()
        first = await _publish(LocalArtifactStore(root_uri=str(root)), root, mcap)

        spy = _SpyStore(root)
        second = await _publish(spy, root, mcap)

        assert spy.writes == []
        assert not second.recording_written and not second.manifest_written
        assert second.manifest_checksum == first.manifest_checksum

    async def test_retry_after_crash_before_manifest_reuses_recording(
        self, root, write_mcap
    ) -> None:
        mcap = write_mcap()
        crashed = _SpyStore(root, fail_suffix="robot_run_manifest.json")
        with pytest.raises(OSError, match="injected"):
            await _publish(crashed, root, mcap)
        # Orphaned recording, no manifest: nothing is published.
        assert crashed.writes == [
            str(root / "robot_runs" / "run-001" / "recording.mcap")
        ]
        assert not await crashed.exists(
            str(root / "robot_runs" / "run-001" / "robot_run_manifest.json")
        )

        retry = _SpyStore(root)
        publication = await _publish(retry, root, mcap)
        assert not publication.recording_written
        assert publication.manifest_written
        assert retry.writes == [publication.manifest_uri]

    async def test_conflicting_recording_fails_without_manifest(
        self, root, write_mcap
    ) -> None:
        store = LocalArtifactStore(root_uri=str(root))
        await _publish(store, root, write_mcap(name="first.mcap"))
        manifest_uri = str(root / "robot_runs" / "run-001" / "robot_run_manifest.json")
        original_manifest = await store.read_bytes(manifest_uri)

        other = write_mcap(
            [("/vehicle/odom", "nav_msgs/msg/Odometry", 5)], name="other.mcap"
        )
        spy = _SpyStore(root)
        with pytest.raises(
            RecordingPublicationConflictError, match="refusing to overwrite"
        ):
            await _publish(spy, root, other)

        assert spy.writes == []
        assert await store.read_bytes(manifest_uri) == original_manifest

    async def test_conflicting_manifest_fails(self, root, write_mcap) -> None:
        mcap = write_mcap()
        store = LocalArtifactStore(root_uri=str(root))
        first = await _publish(store, root, mcap)

        with pytest.raises(RecordingPublicationConflictError):
            await _publish(store, root, mcap, robot_platform="another-platform")

        assert await store.read_bytes(first.manifest_uri) == (
            first.manifest.to_canonical_bytes()
        )


class TestFailedRecordingPublication:
    async def test_write_failure_produces_no_manifest(self, root, write_mcap) -> None:
        store = _SpyStore(root, fail_suffix="recording.mcap")
        with pytest.raises(OSError, match="injected"):
            await _publish(store, root, write_mcap())
        assert store.writes == []
        assert not (
            root / "robot_runs" / "run-001" / "robot_run_manifest.json"
        ).exists()

    async def test_readback_mismatch_produces_no_manifest(
        self, root, write_mcap
    ) -> None:
        store = _SpyStore(root, corrupt_suffix="recording.mcap")
        with pytest.raises(RecordingPublicationIntegrityError):
            await _publish(store, root, write_mcap())
        assert len(store.writes) == 1
        assert not (
            root / "robot_runs" / "run-001" / "robot_run_manifest.json"
        ).exists()


class TestLocalRecordingValidation:
    async def test_partial_path_rejected(self, root, tmp_path) -> None:
        partial = tmp_path / "out" / ".partial" / "run-001" / "run-001_0.mcap"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"x")
        with pytest.raises(RecordingValidationError, match=".partial"):
            await _publish(LocalArtifactStore(root_uri=str(root)), root, partial)

    async def test_missing_file_rejected(self, root, tmp_path) -> None:
        with pytest.raises(RecordingValidationError, match="not found"):
            await _publish(
                LocalArtifactStore(root_uri=str(root)), root, tmp_path / "nope.mcap"
            )

    async def test_corrupt_file_rejected(self, root, tmp_path) -> None:
        bad = tmp_path / "bad.mcap"
        bad.write_bytes(b"definitely not mcap")
        with pytest.raises(RecordingValidationError, match="unreadable or corrupt"):
            await _publish(LocalArtifactStore(root_uri=str(root)), root, bad)

    async def test_truncated_file_rejected(self, root, tmp_path, write_mcap) -> None:
        data = write_mcap().read_bytes()
        truncated = tmp_path / "truncated.mcap"
        truncated.write_bytes(data[: len(data) // 2])
        with pytest.raises(RecordingValidationError):
            await _publish(LocalArtifactStore(root_uri=str(root)), root, truncated)

    async def test_zero_messages_rejected(self, root, write_mcap) -> None:
        with pytest.raises(RecordingValidationError, match="zero messages"):
            await _publish(LocalArtifactStore(root_uri=str(root)), root, write_mcap([]))

    async def test_unsupported_source_clock_rejected(self, root, write_mcap) -> None:
        with pytest.raises(RecordingValidationError, match="source_clock"):
            await _publish(
                LocalArtifactStore(root_uri=str(root)),
                root,
                write_mcap(),
                source_clock="wall_clock",
            )

    async def test_topic_with_two_channel_definitions_rejected(
        self, root, write_mcap
    ) -> None:
        mcap = write_mcap([("/x", "a/msg/A", 1), ("/x", "b/msg/B", 2)])
        with pytest.raises(RecordingValidationError, match="more than one channel"):
            await _publish(LocalArtifactStore(root_uri=str(root)), root, mcap)

    async def test_invalid_run_id_rejected_before_any_write(
        self, root, write_mcap
    ) -> None:
        store = _SpyStore(root)
        with pytest.raises(ValueError, match="run_id"):
            await _publish(store, root, write_mcap(), run_id="../escape")
        assert store.writes == []


class TestCli:
    def test_cli_publishes_and_prints_manifest_uri(
        self, root, write_mcap, monkeypatch, capsys
    ):
        from sceneops_publisher.cli import main

        monkeypatch.setenv("SCENEOPS_PUBLISHER_ARTIFACT__BACKEND", "local")
        monkeypatch.setenv("SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI", str(root))
        mcap = write_mcap()
        argv = [
            "publish",
            "--mcap-path",
            str(mcap),
            "--run-id",
            "run-cli",
            "--robot-id",
            "robot-cli",
            "--source-kind",
            "kafka",
            "--source-topic",
            "t2",
            "--source-topic",
            "t1",
        ]
        assert main(argv) == 0
        result = json.loads(capsys.readouterr().out)
        assert set(result) == {
            "run_id",
            "manifest_uri",
            "manifest_checksum",
            "recording_uri",
            "recording_checksum",
            "recording_size_bytes",
            "recording_written",
            "manifest_written",
        }
        assert result["run_id"] == "run-cli"
        assert result["manifest_uri"] == str(
            root / "robot_runs" / "run-cli" / "robot_run_manifest.json"
        )
        manifest_bytes = Path(result["manifest_uri"]).read_bytes()
        manifest = load_canonical_robot_run_manifest(manifest_bytes)
        assert result["manifest_checksum"] == sha256_checksum(manifest_bytes)
        recording_bytes = mcap.read_bytes()
        assert (
            result["recording_uri"],
            result["recording_checksum"],
            result["recording_size_bytes"],
        ) == (
            manifest.recording.uri,
            sha256_checksum(recording_bytes),
            len(recording_bytes),
        )
        assert (result["recording_written"], result["manifest_written"]) == (
            True,
            True,
        )
        assert manifest.capture.source.topics == ["t1", "t2"]
        assert manifest.robot_platform is None

        # Identical retry is idempotent.
        assert main(argv) == 0
        retry = json.loads(capsys.readouterr().out)
        assert retry["manifest_written"] is False
        assert retry["manifest_checksum"] == result["manifest_checksum"]

    def test_cli_failure_exits_non_zero(self, root, tmp_path, monkeypatch, capsys):
        from sceneops_publisher.cli import main

        monkeypatch.setenv("SCENEOPS_PUBLISHER_ARTIFACT__BACKEND", "local")
        monkeypatch.setenv("SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI", str(root))
        code = main(
            [
                "publish",
                "--mcap-path",
                str(tmp_path / "missing.mcap"),
                "--run-id",
                "r",
                "--robot-id",
                "x",
                "--source-kind",
                "file",
            ]
        )
        assert code == 1
        assert "publish failed" in capsys.readouterr().err


class TestLayerABoundary:
    """ADR-007 §7.3 / I-8: the publisher never depends on the SceneOps DB,
    Celery, or the worker."""

    _FORBIDDEN_ROOTS = {"sceneops_db", "sqlalchemy", "celery", "sceneops_worker", "app"}

    def test_no_source_file_imports_db_or_worker(self) -> None:
        package_root = Path(recording_pkg.__file__).resolve().parent
        offenders = []
        for path in package_root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module] if node.module and node.level == 0 else []
                else:
                    continue
                for name in names:
                    if name.split(".")[0] in self._FORBIDDEN_ROOTS:
                        offenders.append(f"{path.name}: {name}")
        assert offenders == []

    @pytest.mark.parametrize(
        "module",
        ["sceneops_recording", "sceneops_publisher.cli"],
    )
    def test_import_loads_no_db_or_celery(self, module: str) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; import {module}; "
                "loaded = set(sys.modules); "
                "bad = sorted(n for n in loaded if n.split('.')[0] in "
                "{'sqlalchemy', 'sceneops_db', 'celery', 'sceneops_worker', 'asyncpg'}); "
                "assert not bad, bad",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
