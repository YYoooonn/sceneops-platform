"""Harness for the acquisition-recovery fault-injection tests (ADR-008 §8, 12.4).

The tests inject real faults, so they own the pieces a fault would otherwise
take from the shared local stack:

* a throwaway **Redis** container (``RecoveryRedis``) that a test can stop,
  pause and restart -- the dev stack's Redis is never touched;
* Celery **worker subprocesses** (``WorkerProcess``) running
  ``recovery_worker`` on a queue of their own, killable with SIGKILL;
* an isolated **RobotRun root** in MinIO per module (``RecoveryEnv``), so the
  reconciler under test sees only the runs the test published and can never
  act on another suite's objects.

PostgreSQL and MinIO are the live stack's (the semantics under test: row locks,
unique constraints, object reads). Rows are keyed by ``rec124-`` run ids and
removed afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from sceneops_core.config import ArtifactSettings, CelerySettings, ExecutionSettings
from sceneops_core.robots.manifest import CaptureSource, CaptureSourceKind
from sceneops_db.session import get_async_sessionmaker
from sceneops_integrations.recording.publisher import publish_recording_bytes
from sceneops_storage import create_artifact_store

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_MCAP = (
    REPO_ROOT
    / "apps"
    / "worker"
    / "tests"
    / "fixtures"
    / "rosbag"
    / "can_replay_scene_0061.mcap"
)
RUN_PREFIX = "rec124"
REDIS_IMAGE = "redis:7-alpine"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=30
        ).returncode
        == 0
    )


def wait_until(
    predicate: Callable[[], bool], *, timeout: float = 60.0, interval: float = 0.2
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


async def async_wait_until(
    predicate, *, timeout: float = 60.0, interval: float = 0.2
) -> None:
    import asyncio

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RecoveryRedis:
    """A Redis container of the test's own, reachable on a fixed local port so
    stop / pause / restart keep the same address."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.name = f"sceneops-recovery-redis-{uuid.uuid4().hex[:8]}"

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/0"

    @property
    def result_url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/1"

    def _docker(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=120
        )

    def start_new(self) -> None:
        proc = self._docker(
            "run",
            "-d",
            "--name",
            self.name,
            "-p",
            f"127.0.0.1:{self.port}:6379",
            REDIS_IMAGE,
        )
        assert proc.returncode == 0, proc.stderr
        self.wait_ready()

    def wait_ready(self) -> None:
        wait_until(
            lambda: (
                self._docker("exec", self.name, "redis-cli", "ping").stdout.strip()
                == "PONG"
            ),
            timeout=30,
        )

    def stop(self) -> None:
        assert self._docker("stop", "-t", "1", self.name).returncode == 0

    def start(self) -> None:
        assert self._docker("start", self.name).returncode == 0
        self.wait_ready()

    def pause(self) -> None:
        assert self._docker("pause", self.name).returncode == 0

    def unpause(self) -> None:
        assert self._docker("unpause", self.name).returncode == 0

    def flush(self) -> None:
        assert self._docker("exec", self.name, "redis-cli", "FLUSHALL").returncode == 0

    def remove(self) -> None:
        self._docker("rm", "-f", self.name)


@dataclass
class RecoveryEnv:
    """Everything one test module shares."""

    redis: RecoveryRedis
    queue: str
    tmp: Path
    root_prefix: str
    artifact: ArtifactSettings
    fault_file: Path
    marker_dir: Path
    run_ids: list[str] = field(default_factory=list)

    def use_fresh_root(self) -> None:
        """A RobotRun root of its own for the next test: the reconciler scans
        everything under its root, so a test must never inherit another test's
        half-finished runs."""
        base = self.artifact.root_uri.split(f"/{self.root_prefix}")[0]
        self.artifact = self.artifact.model_copy(
            update={"root_uri": f"{base}/{self.root_prefix}/{uuid.uuid4().hex[:8]}"}
        )

    @property
    def robot_run_root(self) -> str:
        return self.artifact.robot_run_root_uri

    def store(self):
        return create_artifact_store(self.artifact)

    def api_settings(self):
        from app.config import ApiSettings

        return ApiSettings(
            artifact=self.artifact,
            execution=ExecutionSettings(
                celery=CelerySettings(
                    broker_url=self.redis.url,
                    result_backend=self.redis.result_url,
                    job_queue=self.queue,
                    task_default_queue=self.queue,
                )
            ),
        )

    # ── faults ──────────────────────────────────────────────────────────────

    def set_faults(self, **faults: list[str]) -> None:
        self.fault_file.write_text(json.dumps(faults))

    def clear_faults(self) -> None:
        self.set_faults()

    def marker(self, fault: str, run_id: str) -> Path:
        return self.marker_dir / f"{fault}.{run_id}"

    # ── publication ─────────────────────────────────────────────────────────

    async def publish_run(self, label: str) -> "Published":
        run_id = f"{RUN_PREFIX}-{label}-{uuid.uuid4().hex[:6]}"
        robot_id = f"{RUN_PREFIX}-robot-{uuid.uuid4().hex[:6]}"
        publication = await publish_recording_bytes(
            artifact_store=self.store(),
            root_uri=self.robot_run_root,
            recording_bytes=FIXTURE_MCAP.read_bytes(),
            run_id=run_id,
            robot_id=robot_id,
            robot_platform="recovery-test",
            capture_source=CaptureSource(
                kind=CaptureSourceKind.KAFKA, topics=["sceneops.robot.telemetry.v1"]
            ),
            source_clock="mcap_log_time",
        )
        self.run_ids.append(run_id)
        return Published(
            run_id=run_id,
            robot_id=robot_id,
            manifest_uri=publication.manifest_uri,
            manifest_checksum=publication.manifest_checksum,
        )


@dataclass(frozen=True)
class Published:
    run_id: str
    robot_id: str
    manifest_uri: str
    manifest_checksum: str


class WorkerProcess:
    """A Celery worker (``recovery_worker``) on the test's own broker and queue.
    ``kill`` is SIGKILL of the whole process group: no cleanup, no ack."""

    def __init__(self, env: RecoveryEnv, name: str) -> None:
        self.env = env
        self.name = name
        self.log_path = env.tmp / f"worker-{name}.log"
        self._proc: subprocess.Popen | None = None

    def _environment(self) -> dict[str, str]:
        art = self.env.artifact
        environment = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [
                    str(REPO_ROOT / "tests" / "infrastructure"),
                    os.environ.get("PYTHONPATH", ""),
                ]
            ),
            "RECOVERY_FAULT_FILE": str(self.env.fault_file),
            "RECOVERY_MARKER_DIR": str(self.env.marker_dir),
            "SCENEOPS_WORKER_ARTIFACT__BACKEND": "minio",
            "SCENEOPS_WORKER_ARTIFACT__ROOT_URI": art.root_uri,
            "SCENEOPS_WORKER_ARTIFACT__ENDPOINT_URL": art.endpoint_url or "",
            "SCENEOPS_WORKER_ARTIFACT__REGION": art.region or "",
            "SCENEOPS_WORKER_ARTIFACT__ACCESS_KEY_ID": art.access_key_id or "",
            "SCENEOPS_WORKER_ARTIFACT__SECRET_ACCESS_KEY": art.secret_access_key or "",
            "SCENEOPS_WORKER_EXECUTION__CELERY__BROKER_URL": self.env.redis.url,
            "SCENEOPS_WORKER_EXECUTION__CELERY__RESULT_BACKEND": self.env.redis.result_url,
            "SCENEOPS_WORKER_EXECUTION__CELERY__JOB_QUEUE": self.env.queue,
            "SCENEOPS_WORKER_EXECUTION__CELERY__TASK_DEFAULT_QUEUE": self.env.queue,
        }
        environment.setdefault(
            "SCENEOPS_WORKER_DATABASE_URL", os.environ["SCENEOPS_DATABASE_URL"]
        )
        return environment

    def start(self) -> "WorkerProcess":
        log = self.log_path.open("ab")
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "recovery_worker.celery_app",
                "worker",
                "--loglevel=INFO",
                f"--queues={self.env.queue}",
                "--concurrency=2",
                f"--hostname={self.name}@%h",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=self._environment(),
            # A cwd without .env.local: the docker hostnames in the dev stack's
            # env file must never leak into a host-side worker.
            cwd=str(self.env.tmp),
            start_new_session=True,
        )
        wait_until(self._ready, timeout=90, interval=0.5)
        return self

    def _ready(self) -> bool:
        assert self._proc is not None
        if self._proc.poll() is not None:
            raise RuntimeError(f"worker exited early:\n{self.log()}")
        return "ready." in self.log()

    def log(self) -> str:
        return (
            self.log_path.read_text(errors="replace") if self.log_path.exists() else ""
        )

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def kill(self) -> None:
        """SIGKILL the worker and its pool children."""
        assert self._proc is not None
        os.killpg(self._proc.pid, signal.SIGKILL)
        self._proc.wait(timeout=30)

    def stop(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        os.killpg(self._proc.pid, signal.SIGTERM)
        try:
            self._proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.kill()

    def __enter__(self) -> "WorkerProcess":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


# ── PostgreSQL probes and cleanup (the tests' own sessions) ──────────────────


@dataclass(frozen=True)
class JobRow:
    job_id: str
    status: str
    error_type: str | None
    created_at: datetime


async def jobs_of(run_id: str) -> list[JobRow]:
    """Every REGISTER_ROBOT_RUN Job whose manifest URI is under ``run_id``,
    oldest first."""
    async with get_async_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "SELECT job_id, status, error->>'type', created_at FROM jobs "
                "WHERE type = 'register_robot_run' "
                "AND params->>'manifest_uri' LIKE :pattern "
                "ORDER BY created_at, job_id"
            ),
            {"pattern": f"%/{run_id}/%"},
        )
        return [JobRow(*row) for row in rows.all()]


async def count_rows(run_id: str) -> dict[str, int]:
    async with get_async_sessionmaker()() as session:
        result = await session.execute(
            text(
                "SELECT "
                " (SELECT count(*) FROM robot_runs WHERE run_id = :run_id),"
                " (SELECT count(*) FROM artifacts WHERE owner_id = :run_id)"
            ),
            {"run_id": run_id},
        )
        runs, artifacts = result.one()
        return {"robot_runs": runs, "artifacts": artifacts}


async def robot_run_checksum(run_id: str) -> str | None:
    async with get_async_sessionmaker()() as session:
        return (
            await session.execute(
                text("SELECT manifest_checksum FROM robot_runs WHERE run_id = :r"),
                {"r": run_id},
            )
        ).scalar_one_or_none()


async def job_row_snapshot(run_id: str) -> list[tuple]:
    async with get_async_sessionmaker()() as session:
        rows = await session.execute(
            text(
                "SELECT job_id, status, error::text, updated_at FROM jobs "
                "WHERE params->>'manifest_uri' LIKE :p ORDER BY job_id"
            ),
            {"p": f"%/{run_id}/%"},
        )
        return [tuple(row) for row in rows.all()]


async def cleanup_rows(run_ids: list[str]) -> None:
    if not run_ids:
        return
    async with get_async_sessionmaker()() as session:
        for run_id in run_ids:
            pattern = f"%/{run_id}/%"
            await session.execute(
                text(
                    "DELETE FROM execution_records WHERE resource_id IN "
                    "(SELECT job_id FROM jobs WHERE params->>'manifest_uri' LIKE :p)"
                ),
                {"p": pattern},
            )
            await session.execute(
                text("DELETE FROM jobs WHERE params->>'manifest_uri' LIKE :p"),
                {"p": pattern},
            )
            await session.execute(
                text("DELETE FROM robot_runs WHERE run_id = :r"), {"r": run_id}
            )
            await session.execute(
                text("DELETE FROM artifacts WHERE owner_id = :r"), {"r": run_id}
            )
        await session.execute(
            text("DELETE FROM robots WHERE robot_id LIKE :p"),
            {"p": f"{RUN_PREFIX}-robot-%"},
        )
        await session.commit()
