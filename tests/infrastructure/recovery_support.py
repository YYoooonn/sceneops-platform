"""Harness for the acquisition-recovery fault-injection tests (ADR-008 §8, 12.4).

The tests inject real faults, so they own the pieces a fault would otherwise
take from the shared local stack:

* a throwaway **Redis** container (``RecoveryRedis``) that a test can stop,
  pause and restart -- the dev stack's Redis is never touched;
* Celery **worker subprocesses** (``WorkerProcess``) running
  ``recovery_worker`` on a queue of their own, killable with SIGKILL;
* an isolated **RobotRun root** in MinIO per test (``RecoveryEnv``), so the
  reconciler under test sees only the runs the test published and can never
  act on another suite's objects.

PostgreSQL and MinIO are real servers (the semantics under test: row locks,
unique constraints, object reads), but not the reference environment's: the suites
run in the disposable database and bucket of ``make test-infrastructure SUITE=recovery``
(``disposable_env.py``), which are dropped as a whole, so no test removes rows or
objects. Rows are keyed by ``rec124-`` run ids.

Shared by ``test_acquisition_recovery.py`` (one fault per test) and
``test_acquisition_lifecycle_acceptance.py`` (the whole lifecycle): the
fixtures, the capture builders (which use Capture's own finalize and receipt
code), the production-command runners and the fault points are the same.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.config import ArtifactSettings, CelerySettings, ExecutionSettings
from sceneops_core.robots.capture_receipt import (
    CaptureReceipt,
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
)
from sceneops_db.session import dispose_async_engine, get_async_sessionmaker
from sceneops_integrations.recording import derive_mcap_facts
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

    # ── production commands ─────────────────────────────────────────────────

    def publisher_environment(self) -> dict[str, str]:
        """Configuration of ``publish-pending`` the way the polling service
        gets it: ArtifactStore settings from the environment, nothing else."""
        art = self.artifact
        return {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [
                    str(REPO_ROOT / "tests" / "infrastructure"),
                    os.environ.get("PYTHONPATH", ""),
                ]
            ),
            "RECOVERY_FAULT_FILE": str(self.fault_file),
            "SCENEOPS_PUBLISHER_ARTIFACT__BACKEND": "minio",
            "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI": art.root_uri,
            "SCENEOPS_PUBLISHER_ARTIFACT__ENDPOINT_URL": art.endpoint_url,
            "SCENEOPS_PUBLISHER_ARTIFACT__REGION": art.region,
            "SCENEOPS_PUBLISHER_ARTIFACT__ACCESS_KEY_ID": art.access_key_id,
            "SCENEOPS_PUBLISHER_ARTIFACT__SECRET_ACCESS_KEY": art.secret_access_key,
        }

    def reconciler_environment(
        self, *, stall_threshold_seconds: float = 30
    ) -> dict[str, str]:
        """Configuration of ``reconcile`` / ``acquisition_status`` as the
        registration-recovery service gets it."""
        art = self.artifact
        return {
            **os.environ,
            "SCENEOPS_API_ARTIFACT__BACKEND": "minio",
            "SCENEOPS_API_ARTIFACT__ROOT_URI": art.root_uri,
            "SCENEOPS_API_ARTIFACT__ENDPOINT_URL": art.endpoint_url,
            "SCENEOPS_API_ARTIFACT__REGION": art.region,
            "SCENEOPS_API_ARTIFACT__ACCESS_KEY_ID": art.access_key_id,
            "SCENEOPS_API_ARTIFACT__SECRET_ACCESS_KEY": art.secret_access_key,
            "SCENEOPS_API_EXECUTION__CELERY__BROKER_URL": self.redis.url,
            "SCENEOPS_API_EXECUTION__CELERY__RESULT_BACKEND": self.redis.result_url,
            "SCENEOPS_API_EXECUTION__CELERY__JOB_QUEUE": self.queue,
            "SCENEOPS_API_RECONCILER__STALL_THRESHOLD_SECONDS": str(
                stall_threshold_seconds
            ),
        }

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
    """A Celery worker (``recovery_worker`` unless ``app`` names another worker
    module of this directory) on the test's own broker and queue. ``kill`` is
    SIGKILL of the whole process group: no cleanup, no ack."""

    def __init__(
        self,
        env: RecoveryEnv,
        name: str,
        *,
        app: str = "recovery_worker.celery_app",
        concurrency: int = 2,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.env = env
        self.name = name
        self.app = app
        self.concurrency = concurrency
        self.extra_env = extra_env or {}
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
            **self.extra_env,
        }
        return environment

    def start(self) -> "WorkerProcess":
        log = self.log_path.open("ab")
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                self.app,
                "worker",
                "--loglevel=INFO",
                f"--queues={self.env.queue}",
                f"--concurrency={self.concurrency}",
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

    def signal(self, sig: int) -> None:
        """Send ``sig`` to the worker and its pool children (SIGSTOP pauses the
        whole worker: alive, holding its claims, doing nothing)."""
        assert self._proc is not None
        os.killpg(self._proc.pid, sig)

    def kill(self) -> None:
        """SIGKILL the worker and its pool children."""
        assert self._proc is not None
        os.killpg(self._proc.pid, signal.SIGKILL)
        self._proc.wait(timeout=30)

    def stop(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        os.killpg(self._proc.pid, signal.SIGCONT)  # a paused worker cannot stop
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


# ── pytest fixtures shared by the recovery suites ────────────────────────────
#
# Registered for the directory by conftest.py. A recovery suite opts in with
# ``pytestmark = pytest.mark.usefixtures(*RECOVERY_FIXTURES)``.

RECOVERY_FIXTURES = ("fresh_engine", "clean_faults_and_queue")


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    if "SCENEOPS_DATABASE_URL" not in os.environ:
        pytest.skip("SCENEOPS_DATABASE_URL not set; run `make test-infrastructure SUITE=recovery`")
    if not docker_available():
        pytest.skip("Docker is needed for the throwaway Redis")

    minio = os.environ.get("MINIO_ENDPOINT_URL", "http://localhost:9000")
    root_prefix = f"recovery_test_{uuid.uuid4().hex[:8]}"
    artifact = ArtifactSettings(
        backend="minio",
        root_uri=f"s3://{os.environ.get('MINIO_BUCKET', 'sceneops')}/artifacts/{root_prefix}",
        endpoint_url=minio,
        region="ap-northeast-2",
        access_key_id=os.environ.get("MINIO_ROOT_USER", "minioadmin"),
        secret_access_key=os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin"),
    )
    tmp = tmp_path_factory.mktemp("recovery")
    marker_dir = tmp / "markers"
    marker_dir.mkdir()
    redis = RecoveryRedis()
    redis.start_new()
    environment = RecoveryEnv(
        redis=redis,
        queue=f"sceneops.recovery-test.{uuid.uuid4().hex[:6]}",
        tmp=tmp,
        root_prefix=root_prefix,
        artifact=artifact,
        fault_file=tmp / "faults.json",
        marker_dir=marker_dir,
    )
    environment.clear_faults()
    try:
        asyncio.run(environment.store().list_objects(environment.robot_run_root))
    except Exception as exc:  # noqa: BLE001
        redis.remove()
        pytest.skip(f"MinIO not reachable at {minio}: {exc}")
    yield environment
    redis.remove()


@pytest_asyncio.fixture
async def fresh_engine():
    """Each test has its own event loop; the process-wide engine must not
    outlive it. Enabled by the recovery suites' ``pytestmark``
    (``RECOVERY_FIXTURES``); not autouse, so the other infrastructure suites
    never start a Redis container."""
    from sceneops_db.session import reset_async_engine_cache

    reset_async_engine_cache()
    yield
    await dispose_async_engine()


@pytest.fixture
def clean_faults_and_queue(env):
    env.use_fresh_root()
    env.clear_faults()
    env.redis.flush()
    for marker in env.marker_dir.iterdir():
        marker.unlink()
    yield


# ── captures, built by Capture's own finalize and receipt code ───────────────


def _load_capture_module(name: str):
    """``ros2/capture/<name>.py`` under a private module name: Capture's
    durability protocol (receipt written before the atomic rename), imported
    without putting its flat module namespace on ``sys.path``."""
    path = REPO_ROOT / "ros2" / "capture" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_sceneops_capture_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_capture_finalize = _load_capture_module("finalize")
_capture_receipt = _load_capture_module("receipt")


def _capture_receipt_for(run_id: str, robot_id: str, data: bytes) -> CaptureReceipt:
    facts = derive_mcap_facts(io.BytesIO(data), source_clock="mcap_log_time")
    return CaptureReceipt(
        run_id=run_id,
        robot_id=robot_id,
        robot_platform="recovery-test",
        recording=ReceiptRecording(
            file=f"{run_id}_0.mcap",
            format=RecordingFormat.MCAP,
            checksum=sha256_checksum(data),
            size_bytes=len(data),
        ),
        capture=CaptureInfo(
            source=CaptureSource(
                kind=CaptureSourceKind.KAFKA, topics=["sceneops.robot.telemetry.v1"]
            ),
            source_clock="mcap_log_time",
        ),
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


def make_finalized_capture(base: Path, run_id: str, robot_id: str) -> Path:
    """A finalized capture exactly as Capture leaves one: the MCAP and its
    ``capture_receipt.json`` written into ``.partial/<run_id>/``, then the
    directory renamed atomically."""
    data = FIXTURE_MCAP.read_bytes()
    partial = _capture_finalize.prepare_partial_bag_dir(base, run_id)
    partial.mkdir()
    (partial / f"{run_id}_0.mcap").write_bytes(data)
    _capture_receipt.write_capture_receipt(
        partial, _capture_receipt_for(run_id, robot_id, data)
    )
    return _capture_finalize.finalize_bag(base, run_id)


def make_unfinished_capture(base: Path, run_id: str) -> Path:
    """A capture killed before finalize: only ``.partial/<run_id>/``."""
    partial = _capture_finalize.prepare_partial_bag_dir(base, run_id)
    partial.mkdir()
    (partial / f"{run_id}_0.mcap").write_bytes(FIXTURE_MCAP.read_bytes()[:1024])
    return partial


def make_legacy_capture(base: Path, run_id: str) -> Path:
    """A finalized bag with no receipt (batch / legacy acquisition)."""
    directory = base / run_id
    directory.mkdir(parents=True)
    (directory / f"{run_id}_0.mcap").write_bytes(FIXTURE_MCAP.read_bytes())
    return directory
