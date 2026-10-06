"""The disposable execution runtime of `make test-infrastructure[-airflow]`.

Infrastructure tests exist to exercise orchestration: forced re-execution, retries,
conflicts, concurrent registration, every pipeline through its orchestrator. Each of
those appends Jobs, PipelineRuns and job-keyed reports, and the platform removes none
of them. The tests therefore run on an execution runtime that is dropped as a whole,
next to the disposable PostgreSQL database and MinIO bucket of `disposable_env.py`:

    create database + bucket  ->  start API / workers / Redis (/ Airflow) wired to them
        ->  run the suite  ->  stop the runtime  ->  drop database + bucket

The runtime is the compose project `sceneops-test` (`compose/test-runtime.yaml`): the
same api and worker images and the same settings as the reference environment, with
the database, the ArtifactStore root and the Celery broker replaced. Only the
PostgreSQL and MinIO servers are shared with the reference environment; its api,
workers, Redis and Airflow are neither used nor reconfigured, so the golden reference
contract is read by nothing and written by nothing.

The minimum reference facts the tests need (one RobotRun of the golden contract's
Recording Import baseline) are not copied from the reference environment: the suite's
`baseline` fixture runs the production create-or-verify path
(`scripts/canonical/canonical_bootstrap.sh`) against this runtime's API, publishing the
locked recording into the disposable bucket (`SCENEOPS_WORKER_ARTIFACT__ROOT_URI`).

A run killed before it could stop the runtime leaves its containers behind; the next
`up` removes them first (the project name is fixed), exactly like the disposable
database and bucket are recreated.
"""

from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

from disposable_env import (
    REPO_ROOT,
    DisposableEnvironment,
    NotDisposableError,
    check_environment,
)

PROJECT = "sceneops-test"
COMPOSE_FILE = REPO_ROOT / "compose" / "test-runtime.yaml"
BACKENDS = ("celery", "airflow")

# How the shared servers are addressed from inside the compose network.
CONTAINER_POSTGRES = "postgres:5432"
ARTIFACT_PREFIX = "artifacts"

# The per-task DAGs the API dispatches to (airflow/dags/sceneops_pipelines.py).
AIRFLOW_DAG_PREFIX = "sceneops"
AIRFLOW_PIPELINES = (
    "recording_scene_building",
    "recording_episode_building",
    "scene_ml_evaluation",
    "episode_learning_data_building",
)

UP_TIMEOUT_S = 300.0
AIRFLOW_UP_TIMEOUT_S = 900.0


class ExecutionRuntimeError(RuntimeError):
    """The execution runtime could not be started or is not what it must be."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@dataclass
class ExecutionRuntime:
    environment: DisposableEnvironment
    pipeline_backend: str = "celery"
    env_file: str = ".env.local"
    api_port: int = field(default_factory=free_port)
    airflow_port: int = field(default_factory=free_port)

    def __post_init__(self) -> None:
        if self.pipeline_backend not in BACKENDS:
            raise ValueError(
                f"pipeline backend {self.pipeline_backend!r}: one of {BACKENDS}"
            )

    # ── configuration ────────────────────────────────────────────────────────

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def artifact_root_uri(self) -> str:
        return f"s3://{self.environment.bucket}/{ARTIFACT_PREFIX}"

    @property
    def database_url(self) -> str:
        """The disposable database as the containers reach it (the `postgres`
        alias of the shared network), not as the host does."""
        server = self.environment.server
        return (
            f"postgresql+asyncpg://{quote(server.user)}:{quote(server.password)}"
            f"@{CONTAINER_POSTGRES}/{self.environment.database}"
        )

    @property
    def profiles(self) -> list[str]:
        return [self.pipeline_backend]

    def compose_environment(self, base: dict[str, str]) -> dict[str, str]:
        """The variables `compose/test-runtime.yaml` interpolates. Every disposable
        name is checked again here, so the runtime cannot be pointed at the
        reference database or bucket even by a caller that bypassed the runner."""
        env = dict(base)
        env.update(
            TEST_DATABASE_URL=self.database_url,
            TEST_ARTIFACT_ROOT_URI=self.artifact_root_uri,
            TEST_PIPELINE_BACKEND=self.pipeline_backend,
            TEST_API_PORT=str(self.api_port),
            TEST_AIRFLOW_PORT=str(self.airflow_port),
            HOST_DATA_DIR=str(REPO_ROOT / "data"),
        )
        check_environment(
            {
                "SCENEOPS_DATABASE_URL": env["TEST_DATABASE_URL"],
                "MINIO_BUCKET": self.environment.bucket,
                "SCENEOPS_WORKER_ARTIFACT__ROOT_URI": env["TEST_ARTIFACT_ROOT_URI"],
            }
        )
        return env

    def child_environment(self, base: dict[str, str]) -> dict[str, str]:
        """What the suite sees on top of the disposable database and bucket: the
        runtime's API, the orchestrator it must report, and the ArtifactStore root
        the compose-run publisher of the baseline fixture writes into."""
        env = dict(base)
        if self.pipeline_backend == "airflow":
            # The Airflow module is opt-in for a plain pytest run; the command that
            # starts Airflow is the explicit request.
            env["SCENEOPS_TEST_AIRFLOW"] = "1"
        env.update(
            SCENEOPS_EXECUTION_RUNTIME="disposable",
            API_BASE_URL=self.api_url,
            EXPECTED_PIPELINE_BACKEND=self.pipeline_backend,
            SCENEOPS_WORKER_ARTIFACT__ROOT_URI=self.artifact_root_uri,
            ENV_FILE=self.env_file,
        )
        check_environment(env)
        return env

    # ── compose ──────────────────────────────────────────────────────────────

    def _compose_command(self, *args: str) -> list[str]:
        command = [
            "docker", "compose",
            "-p", PROJECT,
            "-f", str(COMPOSE_FILE),
            "--project-directory", str(REPO_ROOT),
            "--env-file", str(REPO_ROOT / self.env_file),
        ]  # fmt: skip
        for profile in self.profiles:
            command += ["--profile", profile]
        return command + list(args)

    def _compose(
        self, *args: str, base: dict[str, str], check: bool = True
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            self._compose_command(*args),
            cwd=REPO_ROOT,
            env=self.compose_environment(base),
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise ExecutionRuntimeError(
                f"docker compose {' '.join(args)} failed:\n"
                f"{result.stdout[-2000:]}{result.stderr[-4000:]}"
            )
        return result

    def logs(self, base: dict[str, str], tail: int = 40) -> str:
        result = self._compose(
            "logs", "--no-color", "--tail", str(tail), base=base, check=False
        )
        return result.stdout + result.stderr

    # ── lifecycle ────────────────────────────────────────────────────────────

    def up(self, base: dict[str, str]) -> None:
        """Remove what an interrupted run left, start the runtime, wait until the
        API answers (and, for Airflow, until the pipeline DAGs are loaded), and
        verify that nothing in it points at the reference environment."""
        self.down(base)
        timeout = AIRFLOW_UP_TIMEOUT_S if self.pipeline_backend == "airflow" else UP_TIMEOUT_S
        try:
            self._compose("up", "-d", base=base)
            self._wait_for_api(timeout)
            self._assert_database_reachable()
            self._assert_services_running(base)
            self.verify_isolation(base)
            if self.pipeline_backend == "airflow":
                self._wait_for_airflow_dags(timeout)
        except Exception:
            print(
                f"execution_runtime: start failed; logs:\n{self.logs(base)}",
                flush=True,
            )
            raise

    def down(self, base: dict[str, str]) -> None:
        """Stop and remove every container, network attachment and volume of the
        project. Absent is fine."""
        self._compose(
            "down", "--volumes", "--remove-orphans", "--timeout", "5", base=base
        )

    # ── readiness ────────────────────────────────────────────────────────────

    def _wait_for_api(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.api_url}/health", timeout=3.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1.0)
        raise ExecutionRuntimeError(f"the runtime's API did not answer at {self.api_url}")

    def _assert_database_reachable(self) -> None:
        """A request that reads the database: `/health` does not, so a runtime that
        cannot reach PostgreSQL would otherwise look ready."""
        response = httpx.get(f"{self.api_url}/api/v1/jobs", params={"limit": 1}, timeout=30.0)
        if response.status_code != 200:
            raise ExecutionRuntimeError(
                f"the runtime's API cannot read its database: {response.status_code} "
                f"{response.text[:300]}"
            )

    def _assert_services_running(self, base: dict[str, str]) -> None:
        """Every long-running service is up (a worker that exited on a bad setting
        would otherwise show only as a test timeout)."""
        time.sleep(3.0)
        result = self._compose("ps", "--format", "{{.Service}} {{.State}}", base=base)
        states = dict(line.split(" ", 1) for line in result.stdout.splitlines() if line)
        down = {
            service: state
            for service, state in states.items()
            if state != "running" and service != "test-airflow-init"
        }
        if down or "test-worker-jobs" not in states:
            raise ExecutionRuntimeError(f"runtime services not running: {down or states}")

    def verify_isolation(self, base: dict[str, str]) -> None:
        """Read the settings the running processes actually hold: every service that
        touches PostgreSQL or the ArtifactStore uses the disposable database and
        bucket, and the API runs the orchestrator under test."""
        worker_settings = {
            "SCENEOPS_DATABASE_URL": self.database_url,
            "SCENEOPS_WORKER_DATABASE_URL": self.database_url,
            "SCENEOPS_WORKER_ARTIFACT__ROOT_URI": self.artifact_root_uri,
        }
        for service, variables in {
            "test-api": {
                "SCENEOPS_DATABASE_URL": self.database_url,
                "SCENEOPS_API_DATABASE_URL": self.database_url,
                "SCENEOPS_API_ARTIFACT__ROOT_URI": self.artifact_root_uri,
                "SCENEOPS_API_EXECUTION__PIPELINE_BACKEND": self.pipeline_backend,
            },
            "test-worker-jobs": worker_settings,
            **(
                # What the scheduler holds is forwarded into every task container
                # the DAGs start.
                {"test-airflow-scheduler": worker_settings}
                if self.pipeline_backend == "airflow"
                else {}
            ),
        }.items():
            for name, expected in variables.items():
                actual = self._compose(
                    "exec", "-T", service, "printenv", name, base=base
                ).stdout.strip()
                if actual != expected:
                    raise NotDisposableError(
                        f"{service} holds {name}={actual!r}, expected {expected!r}: "
                        "the runtime is not isolated from the reference environment"
                    )

    def _wait_for_airflow_dags(self, timeout: float) -> None:
        credentials = read_env_file(REPO_ROOT / ".env.airflow.local")
        auth = (credentials["AIRFLOW_ADMIN_USERNAME"], credentials["AIRFLOW_ADMIN_PASSWORD"])
        base_url = f"http://127.0.0.1:{self.airflow_port}/api/v1/dags"
        pending = {f"{AIRFLOW_DAG_PREFIX}_{p}" for p in AIRFLOW_PIPELINES}
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            for dag_id in sorted(pending):
                try:
                    response = httpx.get(f"{base_url}/{dag_id}", auth=auth, timeout=5.0)
                except httpx.HTTPError:
                    continue
                if response.status_code == 200 and not response.json().get("is_paused"):
                    pending.discard(dag_id)
            if pending:
                time.sleep(3.0)
        if pending:
            raise ExecutionRuntimeError(f"Airflow did not load the DAGs {sorted(pending)}")
