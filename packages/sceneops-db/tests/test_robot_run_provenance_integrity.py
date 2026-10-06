"""A RobotRunRecord is immutable recording provenance (ADR-007 §10.4): deleting
the Robot that owns a RobotRun must be refused by the database, leaving the
Robot, the RobotRun and both of its ArtifactRecords untouched.

These tests commit real rows (registration commits too) into the disposable
database of `make test-integration`, which is dropped as a whole."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import (
    robot_run_manifest_artifact_id,
    robot_run_recording_artifact_id,
)
from sceneops_core.robots.schemas import RobotRecord, RobotRunRecord
from sceneops_db.models.robots import RobotModel
from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.robots import (
    PostgresRobotRepository,
    PostgresRobotRunRepository,
)
from sceneops_db.session import get_async_sessionmaker

_FK_NAME = "robot_runs_robot_id_fkey"


async def _register(robot_id: str, run_id: str) -> tuple[str, str]:
    """Commit a Robot, its RobotRun and both RobotRun ArtifactRecords, as one
    REGISTER_ROBOT_RUN transaction would."""
    recording_id = robot_run_recording_artifact_id(run_id)
    manifest_id = robot_run_manifest_artifact_id(run_id)
    async with get_async_sessionmaker()() as session:
        await PostgresRobotRepository(session).create(
            RobotRecord(robot_id=robot_id, platform="test-platform")
        )
        artifacts = PostgresArtifactRefRepository(session)
        for artifact_id, kind, uri in (
            (recording_id, ArtifactKind.ROBOT_RUN_RECORDING, "recording.mcap"),
            (manifest_id, ArtifactKind.ROBOT_RUN_MANIFEST, "robot_run_manifest.json"),
        ):
            await artifacts.create(
                artifact_id=artifact_id,
                ref=ArtifactRef(
                    kind=kind,
                    uri=f"s3://sceneops-test/robot_runs/{run_id}/{uri}",
                    size_bytes=1,
                    checksum="sha256:" + "0" * 64,
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
                source_clock="log_time",
                recording_artifact_id=recording_id,
                manifest_artifact_id=manifest_id,
                manifest_checksum="sha256:" + "1" * 64,
            )
        )
        await session.commit()
    return recording_id, manifest_id


async def _assert_all_present(
    robot_id: str, run_id: str, artifact_ids: tuple[str, str]
) -> None:
    async with get_async_sessionmaker()() as session:
        assert await PostgresRobotRepository(session).get(robot_id) is not None
        run = await PostgresRobotRunRepository(session).get(run_id)
        assert run is not None
        assert run.robot_id == robot_id
        artifacts = PostgresArtifactRefRepository(session)
        for artifact_id in artifact_ids:
            assert await artifacts.get(artifact_id) is not None


@pytest.mark.asyncio
async def test_sql_delete_of_robot_with_robot_run_is_refused(unique_id):
    robot_id, run_id = unique_id("robot"), unique_id("run")
    artifact_ids = await _register(robot_id, run_id)
    async with get_async_sessionmaker()() as session:
        with pytest.raises(IntegrityError, match=_FK_NAME):
            await session.execute(
                text("DELETE FROM robots WHERE robot_id = :robot_id"),
                {"robot_id": robot_id},
            )
        await session.rollback()

    await _assert_all_present(robot_id, run_id, artifact_ids)


@pytest.mark.asyncio
async def test_orm_delete_of_robot_with_loaded_runs_is_refused(unique_id):
    """With ``runs`` loaded, a delete cascade would have deleted the
    RobotRun and a plain relationship would have nulled its robot_id; the
    ORM must do neither and leave the refusal to the database."""
    robot_id, run_id = unique_id("robot"), unique_id("run")
    artifact_ids = await _register(robot_id, run_id)
    async with get_async_sessionmaker()() as session:
        robot = (
            await session.execute(
                select(RobotModel)
                .where(RobotModel.robot_id == robot_id)
                .options(selectinload(RobotModel.runs))
            )
        ).scalar_one()
        assert [r.run_id for r in robot.runs] == [run_id]

        await session.delete(robot)
        with pytest.raises(IntegrityError, match=_FK_NAME):
            await session.flush()
        await session.rollback()

    await _assert_all_present(robot_id, run_id, artifact_ids)


@pytest.mark.asyncio
async def test_robot_without_robot_runs_can_still_be_deleted(db_session, unique_id):
    """RESTRICT only protects Robots that own provenance."""
    robot_id = unique_id("robot")
    await PostgresRobotRepository(db_session).create(RobotRecord(robot_id=robot_id))

    await db_session.execute(delete(RobotModel).where(RobotModel.robot_id == robot_id))

    assert await PostgresRobotRepository(db_session).get(robot_id) is None
