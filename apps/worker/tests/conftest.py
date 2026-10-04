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
import os
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
from sceneops_core.artifacts.schemas.enums import ArtifactBackend
from sceneops_core.common.ids import robot_run_recording_artifact_id
from sceneops_core.robots.schemas import RobotRunRecord
from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)
from sceneops_storage.backends.local import LocalArtifactStore
from sceneops_storage.backends.s3 import S3ArtifactStore
from sceneops_worker.config import WorkerSettings
from sceneops_worker.core import dependencies as dependencies_module
from sceneops_worker.core.dependencies import create_worker_context


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


# ── Canonical Scene harness ─────────────────────────────────────────────────


class _StagedSceneStore:
    """In-memory SceneStore whose writes become visible to other readers
    only on commit, so registrar all-or-nothing behavior is observable."""

    def __init__(self) -> None:
        self.committed: dict = {}
        self.working: dict = {}

    def commit(self) -> None:
        self.committed = dict(self.working)

    def rollback(self) -> None:
        self.working = dict(self.committed)

    async def get(self, scene_id):
        return self.working.get(scene_id)

    async def list(
        self, *, dataset_id=None, dataset_version=None, robot_run_id=None, **_
    ):
        return sorted(
            (
                s
                for s in self.working.values()
                if (dataset_id is None or s.dataset_id == dataset_id)
                and (dataset_version is None or s.dataset_version == dataset_version)
                and (robot_run_id is None or s.robot_run_id == robot_run_id)
            ),
            key=lambda s: s.scene_id,
        )

    async def list_recording_scope(self, *, dataset_id, dataset_version, robot_run_id):
        return await self.list(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            robot_run_id=robot_run_id,
        )

    async def summarize_membership(self, *, dataset_id, dataset_version):
        from sceneops_db.repositories import SceneMembershipSummary

        scenes = await self.list(dataset_id=dataset_id, dataset_version=dataset_version)
        return SceneMembershipSummary(
            scene_count=len(scenes),
            keyframe_count=sum(s.keyframe_count for s in scenes),
            observation_count=sum(s.observation_count for s in scenes),
            observed_channels=sorted({c for s in scenes for c in s.observed_channels}),
        )

    async def insert(self, scene):
        if scene.scene_id in self.working:
            raise RuntimeError(f"duplicate scene {scene.scene_id}")
        self.working[scene.scene_id] = scene
        return scene

    async def replace_revision(self, scene):
        if scene.scene_id not in self.working:
            raise ValueError(f"Scene not found: {scene.scene_id}")
        self.working[scene.scene_id] = scene
        return scene

    async def delete(self, scene_ids):
        for scene_id in scene_ids:
            self.working.pop(scene_id, None)
        return len(scene_ids)


class SceneWorld:
    """A worker context over real canonical manifests in a local
    ArtifactStore, with in-memory record stores."""

    def __init__(self, root) -> None:
        from sceneops_storage.backends.local import LocalArtifactStore
        from sceneops_worker.scenes.artifacts import SceneArtifactStore

        self.root = str(root)
        self.artifact_store = LocalArtifactStore(root_uri=self.root)
        self.scene_artifact_store = SceneArtifactStore(
            artifact_store=self.artifact_store,
            dataset_root_uri=f"{self.root}/datasets",
            payload_root_uri=f"{self.root}/observation_payloads",
        )
        self.artifacts: dict = {}
        self.scenes = _StagedSceneStore()
        self.versions: set = set()
        self.summaries: dict = {}
        self.scene_runs: list = []
        self.robot_runs: dict = {}
        self.commits = 0
        self.rollbacks = 0
        self.context = self._build_context()
        # The RobotRun that sceneops_core.scenes.testing.recording_source()
        # points at by default.
        self.add_robot_run("run-001", recording_checksum="sha256:" + "1" * 64)

    def _build_context(self) -> MagicMock:
        ctx = MagicMock()
        ctx.settings.artifact_root_uri = self.root
        ctx.settings.run_root_uri = f"{self.root}/runs"
        ctx.artifact_store = self.artifact_store
        ctx.scene_artifact_store = self.scene_artifact_store
        ctx.scene_store = self.scenes

        async def get_artifact(artifact_id):
            return self.artifacts.get(artifact_id)

        async def get_artifacts(artifact_ids):
            return {i: self.artifacts[i] for i in artifact_ids if i in self.artifacts}

        async def create_artifact(*, artifact_id, ref, **kwargs):
            from sceneops_core.artifacts.schemas import ArtifactRecord

            record = ArtifactRecord(
                artifact_id=artifact_id,
                kind=ref.kind,
                uri=ref.uri,
                media_type=ref.media_type,
                size_bytes=ref.size_bytes,
                checksum=ref.checksum,
                **{k: v for k, v in kwargs.items() if k != "backend"},
            )
            self.artifacts[artifact_id] = record
            return record

        ctx.artifact_record_store.get = AsyncMock(side_effect=get_artifact)
        ctx.artifact_record_store.get_many = AsyncMock(side_effect=get_artifacts)
        ctx.artifact_record_store.create = AsyncMock(side_effect=create_artifact)

        async def lock(*, dataset_id, version):
            if (dataset_id, version) not in self.versions:
                raise ValueError(f"DatasetVersion not found: {dataset_id}/{version}")

        async def replace_summary(*, dataset_id, version, **summary):
            self.summaries[(dataset_id, version)] = summary

        ctx.dataset_store.lock_version_for_update = AsyncMock(side_effect=lock)
        ctx.dataset_store.replace_scene_membership_summary = AsyncMock(
            side_effect=replace_summary
        )

        async def get_run(run_id):
            return self.robot_runs.get(run_id)

        ctx.robot_store.get_run = AsyncMock(side_effect=get_run)

        async def upsert_run(run):
            self.scene_runs.append(run)
            return run

        ctx.runs.scene_runs.upsert = AsyncMock(side_effect=upsert_run)

        async def commit():
            self.commits += 1
            self.scenes.commit()

        async def rollback():
            self.rollbacks += 1
            self.scenes.rollback()

        ctx.commit = AsyncMock(side_effect=commit)
        ctx.rollback = AsyncMock(side_effect=rollback)
        return ctx

    def add_dataset_version(self, dataset_id: str = "ds", version: str = "v1") -> None:
        self.versions.add((dataset_id, version))

    def add_robot_run(self, run_id: str, *, recording_checksum: str) -> None:
        from datetime import UTC, datetime

        from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord
        from sceneops_core.common.ids import robot_run_recording_artifact_id
        from sceneops_core.robots.schemas import RobotRunRecord

        recording_id = robot_run_recording_artifact_id(run_id)
        self.artifacts[recording_id] = ArtifactRecord(
            artifact_id=recording_id,
            kind=ArtifactKind.ROBOT_RUN_RECORDING,
            uri=f"{self.root}/robot_runs/{run_id}/recording.mcap",
            checksum=recording_checksum,
            size_bytes=1,
        )
        self.robot_runs[run_id] = RobotRunRecord(
            run_id=run_id,
            robot_id="robot-1",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            recording_format="mcap",
            source_clock="mcap_log_time",
            recording_artifact_id=recording_id,
            manifest_artifact_id=f"art-robotrunmanifest-{run_id}",
            manifest_checksum="sha256:" + "0" * 64,
        )

    def manifest(self, **kwargs):
        from sceneops_core.scenes.testing import build_scene_manifest

        return build_scene_manifest(**kwargs)

    @property
    def payload_locator(self):
        from sceneops_worker.scenes.payloads import ArtifactPayloadLocator

        return ArtifactPayloadLocator(self.context.artifact_record_store)

    def payload_uri(self, artifact_id: str) -> str:
        return f"{self.root}/payloads/{artifact_id}"

    async def register_payloads(self, manifest) -> None:
        """Store each observation's payload bytes and register its
        OBSERVATION_PAYLOAD ArtifactRecord, as a producer does."""
        from sceneops_core.scenes.testing import payload_bytes

        for observation in manifest.observations:
            ref = observation.payload
            await self.artifact_store.write_bytes(
                self.payload_uri(ref.artifact_id),
                payload_bytes(observation.observation_id),
            )
            self.artifacts[ref.artifact_id] = ArtifactRecord(
                artifact_id=ref.artifact_id,
                kind=ArtifactKind.OBSERVATION_PAYLOAD,
                uri=self.payload_uri(ref.artifact_id),
                media_type=ref.media_type,
                size_bytes=ref.size_bytes,
                checksum=ref.checksum,
            )

    async def publish(
        self,
        manifest,
        *,
        dataset_id: str = "ds",
        version: str = "v1",
        kind=None,
        with_payloads: bool = True,
    ):
        """Publish a manifest the way a producer does: payload artifacts,
        then write-once manifest bytes at the checksum-qualified key plus its
        SCENE_MANIFEST ArtifactRecord."""
        if with_payloads:
            await self.register_payloads(manifest)
        from sceneops_core.artifacts.schemas import ArtifactKind
        from sceneops_core.scenes.schemas import scene_id_for

        scene_id = scene_id_for(
            dataset_id=dataset_id,
            dataset_version=version,
            source=manifest.lineage.source,
        )
        published = await self.scene_artifact_store.publish_canonical_manifest(
            dataset_id=dataset_id,
            dataset_version=version,
            scene_id=scene_id,
            manifest=manifest,
        )
        artifact_id = f"art-scene-{published.checksum[7:23]}"
        from sceneops_core.artifacts.schemas import ArtifactRecord

        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind or ArtifactKind.SCENE_MANIFEST,
            uri=published.uri,
            media_type="application/json",
            size_bytes=published.size_bytes,
            checksum=published.checksum,
            owner_type="scene",
            owner_id=scene_id,
            dataset_id=dataset_id,
            dataset_version=version,
            scene_id=scene_id,
        )
        self.artifacts[artifact_id] = record
        return record


@pytest.fixture()
def scene_world(tmp_path) -> SceneWorld:
    return SceneWorld(tmp_path / "artifacts")


# ── Real PostgreSQL / MinIO (integration tests only; never autouse) ─────────
#
# Requested only by tests that touch real infrastructure; each skips (not
# fails) when SCENEOPS_DATABASE_URL or MinIO is unavailable.

_BUCKET = os.environ.get("MINIO_BUCKET", "sceneops")
_TEST_PREFIX = "_test-integration-worker"


@pytest.fixture()
def unique_id():
    def _make(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    return _make


@pytest_asyncio.fixture()
async def _fresh_database_connection():
    """Skips (not fails) if Postgres isn't reachable. Requested explicitly
    by db_session and by the concurrency test (which manages its own
    sessions directly, bypassing db_session)."""
    if not os.environ.get("SCENEOPS_DATABASE_URL"):
        pytest.skip(
            "SCENEOPS_DATABASE_URL not set -- run via `make test-integration` "
            "against a running `make local-up` stack."
        )

    reset_async_engine_cache()
    try:
        engine = get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"Postgres not reachable at SCENEOPS_DATABASE_URL: {exc}")

    yield

    await dispose_async_engine()


@pytest_asyncio.fixture()
async def db_session(_fresh_database_connection):
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest.fixture()
def worker_settings(unique_id) -> WorkerSettings:
    """A real WorkerSettings pointed at the local MinIO stack under a
    dedicated, uniquely-prefixed test root -- never the default
    /data/artifacts local backend, and never the shared root real
    RobotRun registrations would use. Never raises on a missing env var
    (falls back to the same local-stack defaults `make local-up` uses) --
    actual reachability is _fresh_database_connection's/
    _minio_reachable's job, so a test can request just this fixture
    without triggering the KeyError a plain os.environ[...] would."""
    return WorkerSettings(
        database_url=os.environ.get(
            "SCENEOPS_DATABASE_URL",
            "postgresql+asyncpg://sceneops:sceneops@localhost:5432/sceneops",
        ),
        artifact={
            "backend": ArtifactBackend.MINIO,
            "root_uri": f"s3://{_BUCKET}/{_TEST_PREFIX}/{unique_id('root')}",
            "endpoint_url": os.environ.get(
                "MINIO_ENDPOINT_URL", "http://localhost:9000"
            ),
            "access_key_id": os.environ.get("MINIO_ROOT_USER", "minioadmin"),
            "secret_access_key": os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
        },
    )


@pytest_asyncio.fixture()
async def _minio_reachable(worker_settings):
    """Skips (not fails) if MinIO isn't reachable. Independent of Postgres
    reachability -- requested explicitly wherever MinIO is touched."""
    probe = S3ArtifactStore(settings=worker_settings.artifact)
    try:
        await probe.exists(f"{worker_settings.artifact_root_uri}/_connectivity_check")
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"MinIO not reachable: {exc}")


@pytest_asyncio.fixture()
async def worker_context(db_session, worker_settings, _minio_reachable):
    """A real WorkerContext (real Postgres session + real MinIO-backed
    ArtifactStore). create_worker_context() caches its ArtifactStore in a
    process-global (by design, for a long-running worker process) --
    reset it first so this test's MinIO-pointed settings are actually
    used rather than silently reusing whatever backend an earlier test in
    the same pytest process initialized."""
    dependencies_module._artifact_store = None
    dependencies_module._raw_source_store = None

    context = create_worker_context(
        db_session, settings=worker_settings, worker_id="test"
    )

    yield context

    dependencies_module._artifact_store = None
    dependencies_module._raw_source_store = None


@pytest_asyncio.fixture()
async def cleanup_minio_prefix(worker_settings):
    store = S3ArtifactStore(settings=worker_settings.artifact)
    yield
    await store.delete_prefix(worker_settings.artifact_root_uri)
