"""RobotRun publication fixtures. The real-Postgres / real-MinIO
fixtures (db_session, worker_settings, worker_context, ...) live in the
worker test conftest and are shared with the Scene registration tests."""

from __future__ import annotations

import pytest

from sceneops_storage.backends.s3 import S3ArtifactStore


@pytest.fixture()
def publish(worker_settings):
    """Publish a local MCAP through the real (DB-free) Recording Publisher
    into this test's unique MinIO prefix -- the same prefix the worker
    context reads from."""
    from sceneops_core.robots.manifest import CaptureSource, CaptureSourceKind
    from sceneops_integrations.recording import publish_recording

    async def _publish(
        mcap_path,
        *,
        run_id: str,
        robot_id: str,
        robot_platform: str | None = None,
    ):
        return await publish_recording(
            artifact_store=S3ArtifactStore(settings=worker_settings.artifact),
            root_uri=worker_settings.artifact.robot_run_root_uri,
            recording_path=mcap_path,
            run_id=run_id,
            robot_id=robot_id,
            robot_platform=robot_platform,
            capture_source=CaptureSource(kind=CaptureSourceKind.FILE),
            source_clock="mcap_log_time",
        )

    return _publish
