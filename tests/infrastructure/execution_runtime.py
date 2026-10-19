"""The disposable execution runtime of `make test-infrastructure` (SUITE=pipelines).

Infrastructure tests exist to exercise orchestration: forced re-execution, retries,
conflicts, concurrent registration, every pipeline through its orchestrator. Each of
those appends Jobs, PipelineRuns and job-keyed reports, and the platform removes none
of them. The tests therefore run on an execution runtime that is dropped as a whole,
next to the disposable PostgreSQL database and MinIO bucket of `disposable_env.py`:

    create database + bucket  ->  start API / workers / Redis wired to them
        ->  run the suite  ->  stop the runtime  ->  drop database + bucket

The runtime is the compose project `sceneops-test` (`compose/test-runtime.yaml`): the
same api and worker images and the same settings as the reference environment, with
the database, the ArtifactStore root and the Celery broker replaced. Only the
PostgreSQL and MinIO servers are shared with the reference environment; its api,
workers and Redis are neither used nor reconfigured, so the golden reference
contract is read by nothing and written by nothing.

The minimum reference facts the tests need (one RobotRun of the golden contract's
Recording Import baseline) are not copied from the reference environment: the suite's
`baseline` fixture runs the production create-or-verify path
(`tools/baselines/canonical/canonical_bootstrap.sh`) against this runtime's API, publishing the
locked recording into the disposable bucket (`SCENEOPS_WORKER_ARTIFACT__ROOT_URI`).

With ``streaming`` the project also loads `compose/test-streaming.yaml`: a Kafka
broker, a capture volume and the replay / bridge / Capture / publisher services of
its own, so a streamed RobotRun is acquired through the production path
(tools/baselines/streaming/streaming_lib.sh, STREAMING_RUNTIME=disposable) without
touching the reference environment's broker, Kafka log or capture volume.

A run killed before it could stop the runtime leaves its containers behind; the next
`up` removes them first (the project name is fixed, and `down` always covers the
streaming services and volumes too), exactly like the disposable database and bucket
are recreated.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from dataclasses import dataclass, field
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
STREAMING_COMPOSE_FILE = REPO_ROOT / "compose" / "test-streaming.yaml"
# compose/test-streaming.yaml's services started per command (`docker compose run`).
ONESHOT_PROFILE = "oneshot"
STREAMING_KAFKA = "test-kafka:9092"

# How the shared servers are addressed from inside the compose network.
CONTAINER_POSTGRES = "postgres:5432"
ARTIFACT_PREFIX = "artifacts"

UP_TIMEOUT_S = 300.0


class ExecutionRuntimeError(RuntimeError):
    """The execution runtime could not be started or is not what it must be."""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class ExecutionRuntime:
    environment: DisposableEnvironment
    env_file: str = ".env.local"
    api_port: int = field(default_factory=free_port)
    streaming: bool = False

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

    def compose_environment(self, base: dict[str, str]) -> dict[str, str]:
        """The variables `compose/test-runtime.yaml` interpolates. Every disposable
        name is checked again here, so the runtime cannot be pointed at the
        reference database or bucket even by a caller that bypassed the runner."""
        env = dict(base)
        env.update(
            TEST_DATABASE_URL=self.database_url,
            TEST_ARTIFACT_ROOT_URI=self.artifact_root_uri,
            TEST_API_PORT=str(self.api_port),
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
        runtime's API and the ArtifactStore root the compose-run publisher of the
        baseline fixture writes into."""
        env = dict(base)
        env.update(
            SCENEOPS_EXECUTION_RUNTIME="disposable",
            API_BASE_URL=self.api_url,
            SCENEOPS_WORKER_ARTIFACT__ROOT_URI=self.artifact_root_uri,
            ENV_FILE=self.env_file,
        )
        if self.streaming:
            # streaming_lib.sh drives this project's streaming services itself, so
            # it needs the variables the compose files interpolate (checked again).
            env = self.compose_environment(env)
            env["STREAMING_RUNTIME"] = "disposable"
        check_environment(env)
        return env

    # ── compose ──────────────────────────────────────────────────────────────

    def _compose_command(self, *args: str, streaming: bool | None = None) -> list[str]:
        files = [COMPOSE_FILE]
        if self.streaming if streaming is None else streaming:
            files.append(STREAMING_COMPOSE_FILE)
        command = ["docker", "compose", "-p", PROJECT]
        for path in files:
            command += ["-f", str(path)]
        command += [
            "--project-directory", str(REPO_ROOT),
            "--env-file", str(REPO_ROOT / self.env_file),
        ]  # fmt: skip
        return command + list(args)

    def _compose(
        self,
        *args: str,
        base: dict[str, str],
        check: bool = True,
        streaming: bool | None = None,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            self._compose_command(*args, streaming=streaming),
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
        API answers, and verify that nothing in it points at the reference
        environment."""
        self.down(base)
        try:
            self._compose("up", "-d", base=base)
            self._wait_for_api(UP_TIMEOUT_S)
            self._assert_database_reachable()
            self._assert_services_running(base)
            self.verify_isolation(base)
        except Exception:
            print(
                f"execution_runtime: start failed; logs:\n{self.logs(base)}",
                flush=True,
            )
            raise

    def down(self, base: dict[str, str]) -> None:
        """Stop and remove every container, network and volume of the project, the
        streaming ones included whether or not this runtime started them (a killed
        streaming run leaves its broker and volumes). Absent is fine."""
        self._compose(
            "--profile", ONESHOT_PROFILE,
            "down", "--volumes", "--remove-orphans", "--timeout", "5",
            base=base, streaming=True,
        )  # fmt: skip

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
        down = {service: state for service, state in states.items() if state != "running"}
        required = {"test-worker-jobs", "test-worker-pipeline"}
        if self.streaming:
            required.add("test-kafka")
        if down or not required <= states.keys():
            raise ExecutionRuntimeError(f"runtime services not running: {down or states}")

    def verify_isolation(self, base: dict[str, str]) -> None:
        """Read the settings the running processes actually hold: every service that
        touches PostgreSQL or the ArtifactStore uses the disposable database and
        bucket."""
        worker_settings = {
            "SCENEOPS_DATABASE_URL": self.database_url,
            "SCENEOPS_WORKER_ARTIFACT__ROOT_URI": self.artifact_root_uri,
        }
        for service, variables in {
            "test-api": {
                "SCENEOPS_DATABASE_URL": self.database_url,
                "SCENEOPS_API_ARTIFACT__ROOT_URI": self.artifact_root_uri,
            },
            "test-worker-jobs": worker_settings,
            "test-worker-pipeline": worker_settings,
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
        if self.streaming:
            self._verify_streaming_isolation(base)

    def _verify_streaming_isolation(self, base: dict[str, str]) -> None:
        """The one-shot streaming services are not running yet: read the
        configuration `docker compose run` will start them with."""
        result = self._compose(
            "--profile", ONESHOT_PROFILE, "config", "--format", "json", base=base
        )  # fmt: skip
        problems = streaming_isolation_problems(
            json.loads(result.stdout)["services"], self.artifact_root_uri
        )
        if problems:
            raise NotDisposableError(
                "the streaming services are not isolated from the reference "
                f"environment: {problems}"
            )


def streaming_isolation_problems(
    services: dict[str, dict], artifact_root_uri: str
) -> list[str]:
    """What in a resolved compose configuration would let the streaming path reach
    the reference environment: the broker address of the bridge and Capture, the
    ArtifactStore root of the publisher, and the networks of the DDS / Kafka
    participants."""
    problems = []
    for name in ("test-streaming-bridge", "test-capture"):
        env = services.get(name, {}).get("environment") or {}
        if env.get("SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS") != STREAMING_KAFKA:
            problems.append(f"{name} does not use {STREAMING_KAFKA}")
    root = (
        (services.get("test-recording-publisher", {}).get("environment") or {})
        .get("SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI")
    )
    if root != artifact_root_uri:
        problems.append(f"test-recording-publisher writes to {root!r}")
    for name in ("test-kafka", "test-streaming-bridge", "test-capture", "test-dataset-replay"):
        networks = set(services.get(name, {}).get("networks") or {})
        if networks != {"streaming"}:
            problems.append(f"{name} is on networks {sorted(networks)}, not only the private one")
    return problems
