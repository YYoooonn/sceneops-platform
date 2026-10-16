"""Shared fixtures for sceneops-db integration tests against a real Postgres.

These tests require SCENEOPS_DATABASE_URL to point at a real, migrated
Postgres instance. `make test-integration` sets it automatically against the
local stack (`make local-up` must be running first). If it's missing or the
database is unreachable, tests are skipped rather than erroring.

The engine/sessionmaker in sceneops_db.session are process-lifetime
singletons (by design — that's the right shape for a long-running API/worker
process). pytest-asyncio gives each test function its own event loop by
default, so that singleton must be reset every test, or the second test to
run reuses a connection pool bound to the first test's (now-closed) loop.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)


@pytest.fixture()
def unique_id():
    """A per-test-call identity generator, isolated from other developers'
    persistent local-stack data and from other test runs.

    A fixture (not a plain importable helper) deliberately: this test
    directory has no __init__.py, so test modules can't do a package-
    relative import of it — importlib import-mode would otherwise collide
    "tests.conftest" here with packages/sceneops-storage/tests/conftest.py's
    identically-named module when both are collected in one `pytest` run
    (as `make test-integration` does)."""

    def _make(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    return _make


@pytest_asyncio.fixture(autouse=True)
async def _fresh_database_connection():
    """Runs before/after every test: fresh engine bound to this test's event
    loop, skip (not fail) if Postgres isn't reachable, dispose afterward."""
    if not os.environ.get("SCENEOPS_DATABASE_URL"):
        pytest.skip(
            "SCENEOPS_DATABASE_URL not set — sceneops-db integration tests need "
            "a real Postgres instance. Run via `make test-integration` against "
            "a running `make local-up` stack."
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
async def db_session():
    """A real session, rolled back (not committed) at teardown so tests
    never depend on — or pollute — persistent local-stack state beyond the
    uniquely-identified rows they explicitly create."""
    sessionmaker = get_async_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest.fixture()
def seed_dataset_version():
    """Create a Dataset + DatasetVersion row (Scenes reference their
    DatasetVersion by foreign key)."""
    from sceneops_core.datasets.schemas.records import (
        DatasetRecord,
        DatasetVersionRecord,
    )
    from sceneops_db.postgres.datasets import (
        PostgresDatasetRepository,
        PostgresDatasetVersionRepository,
    )

    async def _seed(session, *, dataset_id: str, version: str = "v1") -> None:
        if await PostgresDatasetRepository(session).get(dataset_id) is None:
            await PostgresDatasetRepository(session).create(
                DatasetRecord(dataset_id=dataset_id)
            )
        await PostgresDatasetVersionRepository(session).create(
            DatasetVersionRecord(dataset_id=dataset_id, version=version)
        )

    return _seed


@pytest.fixture()
def seed_robot_run(unique_id):
    """Create a registered RobotRun (Robot + both RobotRun ArtifactRecords +
    RobotRunRecord) so recording-derived Scenes can reference it."""
    from datetime import UTC, datetime

    from sceneops_core.artifacts.schemas import (
        ArtifactKind,
        ArtifactOwnerType,
        ArtifactRef,
    )
    from sceneops_core.common.ids import (
        robot_run_manifest_artifact_id,
        robot_run_recording_artifact_id,
    )
    from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
    from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
    from sceneops_db.postgres.robots import (
        PostgresRobotRepository,
        PostgresRobotRunRepository,
    )

    async def _seed(session, *, run_id: str, recording_checksum: str) -> None:
        robot_id = unique_id("robot")
        await PostgresRobotRepository(session).create(RobotRecord(robot_id=robot_id))
        artifacts = PostgresArtifactRefRepository(session)
        for artifact_id, kind, checksum in (
            (
                robot_run_recording_artifact_id(run_id),
                ArtifactKind.ROBOT_RUN_RECORDING,
                recording_checksum,
            ),
            (
                robot_run_manifest_artifact_id(run_id),
                ArtifactKind.ROBOT_RUN_MANIFEST,
                "sha256:" + "0" * 64,
            ),
        ):
            await artifacts.create(
                artifact_id=artifact_id,
                ref=ArtifactRef(
                    kind=kind,
                    uri=f"s3://sceneops-test/robot_runs/{run_id}/{artifact_id}",
                    size_bytes=1,
                    checksum=checksum,
                ),
                owner_type=ArtifactOwnerType.ROBOT_RUN,
                owner_id=run_id,
            )
        await PostgresRobotRunRepository(session).create(
            RobotRunRecord(
                run_id=run_id,
                robot_id=robot_id,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                ended_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
                recording_format="mcap",
                source_clock="mcap_log_time",
                recording_artifact_id=robot_run_recording_artifact_id(run_id),
                manifest_artifact_id=robot_run_manifest_artifact_id(run_id),
                manifest_checksum="sha256:" + "0" * 64,
            )
        )

    return _seed


async def _register_manifest_artifact(
    session,
    *,
    artifact_id,
    kind,
    uri,
    data,
    checksum,
    owner_type,
    owner_id,
    scene_id=None,
):
    """The manifest ArtifactRecord a build publishes, once per revision."""
    from sceneops_core.artifacts.schemas import ArtifactRef
    from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository

    repo = PostgresArtifactRefRepository(session)
    if await repo.get(artifact_id) is not None:
        return
    await repo.create(
        artifact_id=artifact_id,
        ref=ArtifactRef(
            kind=kind,
            uri=uri,
            media_type="application/json",
            size_bytes=len(data),
            checksum=checksum,
        ),
        owner_type=owner_type,
        owner_id=owner_id,
        scene_id=scene_id,
    )


@pytest.fixture()
def scene_revision_for(unique_id, seed_robot_run):
    """Build a canonical SceneManifest, register its SCENE_MANIFEST
    ArtifactRecord under the revision's deterministic id (and, unless
    ``seed_run=False``, the RobotRun its source names), and return the
    SceneRevisionRecord projection (not registered)."""
    from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType
    from sceneops_core.provenance import manifest_artifact_id_for
    from sceneops_core.scenes.schemas import project_scene_revision, scene_id_for
    from sceneops_core.scenes.testing import build_scene_manifest, recording_source
    from sceneops_db.postgres.robots import PostgresRobotRunRepository

    async def _make(session, *, source=None, seed_run: bool = True, **manifest_kwargs):
        if source is None:
            source = recording_source(robot_run_id=unique_id("run"))
        if (
            seed_run
            and await PostgresRobotRunRepository(session).get(source.robot_run_id)
            is None
        ):
            await seed_robot_run(
                session,
                run_id=source.robot_run_id,
                recording_checksum=source.recording_checksum,
            )
        manifest = build_scene_manifest(source=source, **manifest_kwargs)
        data = manifest.to_canonical_bytes()
        checksum = manifest.checksum()
        scene_id = scene_id_for(source)
        artifact_id = manifest_artifact_id_for(
            unit_id=scene_id, manifest_checksum=checksum
        )
        await _register_manifest_artifact(
            session,
            artifact_id=artifact_id,
            kind=ArtifactKind.SCENE_MANIFEST,
            uri=f"s3://sceneops-test/canonical/scenes/{scene_id}/{artifact_id}.json",
            data=data,
            checksum=checksum,
            owner_type=ArtifactOwnerType.SCENE,
            owner_id=scene_id,
            scene_id=scene_id,
        )
        return project_scene_revision(
            manifest=manifest,
            manifest_artifact_id=artifact_id,
            manifest_checksum=checksum,
        )

    return _make


@pytest.fixture()
def episode_revision_for(unique_id, seed_robot_run):
    """Build a canonical EpisodeManifest, register its EPISODE_MANIFEST
    ArtifactRecord under the revision's deterministic id (and, unless
    ``seed_run=False``, the RobotRun it names), and return the
    EpisodeRevisionRecord projection (not registered)."""
    from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactOwnerType
    from sceneops_core.episodes.schemas import (
        episode_id_for,
        project_episode_revision,
    )
    from sceneops_core.episodes.testing import action, episode_manifest, state
    from sceneops_core.provenance import manifest_artifact_id_for
    from sceneops_db.postgres.robots import PostgresRobotRunRepository

    async def _make(
        session,
        *,
        robot_run_id: str | None = None,
        unit_key: str = "recording",
        seed_run: bool = True,
        x: float = 1.0,
        build_config: dict | None = None,
    ):
        robot_run_id = robot_run_id or unique_id("run")
        manifest = episode_manifest(
            [state("/odom", 0, x=x), action("/control", 5, u=0.1)],
            robot_run_id=robot_run_id,
            unit_key=unit_key,
            build_config=build_config,
        )
        source = manifest.lineage.source
        if (
            seed_run
            and await PostgresRobotRunRepository(session).get(robot_run_id) is None
        ):
            await seed_robot_run(
                session,
                run_id=robot_run_id,
                recording_checksum=source.recording_checksum,
            )
        data = manifest.to_canonical_bytes()
        checksum = manifest.checksum()
        episode_id = episode_id_for(source)
        artifact_id = manifest_artifact_id_for(
            unit_id=episode_id, manifest_checksum=checksum
        )
        await _register_manifest_artifact(
            session,
            artifact_id=artifact_id,
            kind=ArtifactKind.EPISODE_MANIFEST,
            uri=f"s3://sceneops-test/canonical/episodes/{episode_id}/{artifact_id}.json",
            data=data,
            checksum=checksum,
            owner_type=ArtifactOwnerType.EPISODE,
            owner_id=episode_id,
        )
        return project_episode_revision(
            manifest=manifest,
            manifest_artifact_id=artifact_id,
            manifest_checksum=checksum,
        )

    return _make


@pytest.fixture()
def pin_members():
    """Pin revisions (Scene or Episode) as members of a DatasetVersion."""
    from sceneops_core.datasets.schemas import DatasetVersionMember
    from sceneops_db.postgres.datasets import PostgresDatasetVersionMemberRepository

    async def _pin(session, *, dataset_id: str, version: str = "v1", revisions):
        members = []
        for revision in revisions:
            domain = "scene" if hasattr(revision, "scene_id") else "episode"
            members.append(
                DatasetVersionMember(
                    domain=domain,
                    unit_id=getattr(revision, f"{domain}_id"),
                    manifest_artifact_id=revision.manifest_artifact_id,
                    manifest_checksum=revision.manifest_checksum,
                )
            )
        await PostgresDatasetVersionMemberRepository(session).add(
            dataset_id=dataset_id, version=version, members=members
        )

    return _pin
