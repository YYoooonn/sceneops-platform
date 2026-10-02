"""Worker-wide test fixtures.

``register_local_recording`` stands in for REGISTER_ROBOT_RUN in handler unit
tests that use a MagicMock WorkerContext: it stores recording bytes in a real
``LocalArtifactStore`` and wires the context's ``robot_store`` /
``artifact_record_store`` lookups to a matching RobotRunRecord + recording
ArtifactRecord, so recording consumers run the real verified recording
resolver instead of a patched one. ``context.artifact_store`` becomes a
``MagicMock`` wrapping the real store, so call assertions keep working.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_storage.backends.local import LocalArtifactStore


@pytest.fixture()
def register_local_recording(tmp_path):
    root_uri = str(tmp_path / "artifacts")

    async def _register(
        context: MagicMock,
        data: bytes,
        *,
        run_id: str = "run-1",
        robot_id: str = "robot-1",
    ) -> tuple[RobotRunRecord, ArtifactRecord]:
        store = LocalArtifactStore(root_uri=root_uri)
        uri = store.join_uri(root_uri, "robot_runs", run_id, "recording.mcap")
        await store.write_bytes(uri, data)

        artifact = ArtifactRecord(
            artifact_id=robot_run_recording_artifact_id(run_id),
            kind=ArtifactKind.ROBOT_RUN_RECORDING,
            uri=uri,
            checksum=f"sha256:{hashlib.sha256(data).hexdigest()}",
            size_bytes=len(data),
        )
        robot_run = RobotRunRecord(
            run_id=run_id,
            robot_id=robot_id,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            recording_format="mcap",
            source_clock="mcap_log_time",
            recording_artifact_id=artifact.artifact_id,
            manifest_artifact_id=f"art-robotrunmanifest-{run_id}",
            manifest_checksum="sha256:" + "1" * 64,
        )

        context.artifact_store = MagicMock(spec=store, wraps=store)
        context.robot_store.get_run = AsyncMock(
            side_effect=lambda rid: robot_run if rid == run_id else None
        )
        context.artifact_record_store.get = AsyncMock(
            side_effect=lambda aid: artifact if aid == artifact.artifact_id else None
        )
        return robot_run, artifact

    return _register
