"""Unit tests of the disposable execution runtime's wiring (no Docker, PostgreSQL or MinIO).

Starting the runtime and running the suites on it is what `make test-infrastructure`
does; these tests pin the rules that keep it isolated from the reference environment.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

import disposable_env as de
import execution_runtime as er

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER = de.Server.from_environ({})
COMPOSE = yaml.safe_load(er.COMPOSE_FILE.read_text())


def _runtime() -> er.ExecutionRuntime:
    environment = de.DisposableEnvironment.checked(
        database="sceneops_test", bucket="sceneops-test", server=SERVER
    )
    return er.ExecutionRuntime(environment, api_port=18000)


def test_the_runtime_addresses_the_disposable_database_as_the_containers_see_it():
    runtime = _runtime()
    assert runtime.database_url.endswith("@postgres:5432/sceneops_test")
    assert runtime.artifact_root_uri == "s3://sceneops-test/artifacts"
    env = runtime.compose_environment({"PATH": "/bin"})
    assert env["TEST_DATABASE_URL"] == runtime.database_url
    assert env["TEST_ARTIFACT_ROOT_URI"] == "s3://sceneops-test/artifacts"
    assert env["TEST_API_PORT"] == "18000"
    assert env["PATH"] == "/bin"


@pytest.mark.parametrize(
    "database, bucket",
    [("sceneops", "sceneops-test"), ("sceneops_test", "sceneops")],
)
def test_a_runtime_on_the_reference_names_cannot_produce_a_compose_environment(
    database, bucket
):
    # Built around the checked constructor on purpose: the runtime re-checks.
    environment = de.DisposableEnvironment(database=database, bucket=bucket, server=SERVER)
    with pytest.raises(de.NotDisposableError):
        er.ExecutionRuntime(environment).compose_environment({})


def test_the_suite_sees_the_runtimes_api_and_the_disposable_artifact_root():
    child = _runtime().child_environment({})
    assert child["API_BASE_URL"] == "http://127.0.0.1:18000"
    assert child["SCENEOPS_EXECUTION_RUNTIME"] == "disposable"
    # The compose-run publisher of the baseline fixture reads this variable.
    assert child["SCENEOPS_WORKER_ARTIFACT__ROOT_URI"] == "s3://sceneops-test/artifacts"


def test_a_publisher_aimed_at_the_reference_bucket_is_refused():
    with pytest.raises(de.NotDisposableError):
        de.check_environment(
            {"SCENEOPS_WORKER_ARTIFACT__ROOT_URI": "s3://sceneops/artifacts"}
        )
    de.check_environment(
        {"SCENEOPS_WORKER_ARTIFACT__ROOT_URI": "s3://sceneops-test/artifacts"}
    )


# ── compose/test-runtime.yaml ────────────────────────────────────────────────


def _environment(service: dict) -> dict:
    environment = service.get("environment", {})
    if isinstance(environment, list):
        environment = dict(item.split("=", 1) for item in environment)
    return {k: str(v) for k, v in environment.items()}


def test_every_service_is_named_apart_from_the_reference_stack():
    """The project joins the reference network, where service names are aliases: a
    shared name would split traffic between two stacks."""
    assert all(name.startswith("test-") for name in COMPOSE["services"])


@pytest.mark.parametrize(
    "service",
    [
        "test-api",
        "test-worker-jobs",
        "test-worker-pipeline",
    ],
)
def test_every_service_that_touches_state_takes_its_database_and_bucket_from_the_runner(
    service,
):
    env = _environment(COMPOSE["services"][service])
    assert env["SCENEOPS_DATABASE_URL"] == "${TEST_DATABASE_URL:?}", service
    root = (
        "SCENEOPS_API_ARTIFACT__ROOT_URI"
        if service == "test-api"
        else "SCENEOPS_WORKER_ARTIFACT__ROOT_URI"
    )
    assert env[root] == "${TEST_ARTIFACT_ROOT_URI:?}", service


def test_the_broker_is_the_runtimes_own_redis():
    for name, service in COMPOSE["services"].items():
        for variable, value in _environment(service).items():
            if "BROKER_URL" in variable or "RESULT_BACKEND" in variable:
                assert value.startswith("redis://test-redis:"), (name, variable)
    assert "ports" not in COMPOSE["services"]["test-redis"]


def test_published_ports_are_loopback_only():
    for name, service in COMPOSE["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (name, port)


def test_the_compose_file_names_no_reference_database_bucket_or_service():
    text = er.COMPOSE_FILE.read_text()
    assert not re.search(r"sceneops/artifacts|/sceneops\b(?!-)|s3://sceneops/", text)
    for reference in ("minio", "redis"):
        # Only the shared PostgreSQL / MinIO servers are reached, through the
        # settings the runner supplies; the reference Redis is never addressed.
        assert f"redis://{reference}:" not in text


def test_the_runtime_orchestrates_and_executes_jobs_in_separate_workers():
    """Pipeline orchestration and Job execution are separate queues and workers, and
    every service always starts: there is no orchestrator variant to select."""
    services = COMPOSE["services"]
    assert "--queues=sceneops.pipeline_runs" in services["test-worker-pipeline"]["command"]
    assert "--queues=sceneops.jobs" in services["test-worker-jobs"]["command"]
    assert not any("profiles" in service for service in services.values())


def test_the_runtime_joins_the_reference_network_to_reach_the_shared_servers():
    default = COMPOSE["networks"]["default"]
    assert default == {"name": "sceneops-network", "external": True}


def test_the_runtime_is_not_part_of_the_reference_compose_project():
    assert "test-runtime.yaml" not in (REPO_ROOT / "compose.yaml").read_text()


# ── Make wiring ──────────────────────────────────────────────────────────────


def _recipe(target: str) -> str:
    text = "".join(p.read_text() for p in (REPO_ROOT / "makefiles").glob("*.mk"))
    match = re.search(rf"^{target}:.*?(?=^\.PHONY|^[a-z-]+:|\Z)", text, re.S | re.M)
    assert match, target
    return match.group(0)


def test_the_pipelines_suite_runs_on_the_disposable_execution_runtime():
    target = "infra-suite-pipelines"
    recipe = _recipe(target)
    assert "$(DISPOSABLE_ENV_RUNTIME) --" in recipe
    assert "$(DISPOSABLE_PYTEST)" in recipe
    assert "SCENEOPS_DATABASE_URL" not in recipe
    # Neither bootstraps nor addresses the reference environment.
    first_line = recipe.splitlines()[0]
    assert first_line.strip() == f"{target}:", first_line
    assert "API_BASE_URL" not in recipe


def _make(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "suite", ["pipelines", "recovery", "kafka", "boundaries"]
)
def test_test_infrastructure_dispatches_each_suite_to_its_own_target(suite):
    """`SUITE=` only selects; the isolation of a suite is its own recipe's."""
    dry_run = _make("-n", "test-infrastructure", f"SUITE={suite}")
    assert dry_run.returncode == 0, dry_run.stderr
    assert f"infra-suite-{suite}" in dry_run.stdout


def test_test_infrastructure_defaults_to_the_pipelines_suite():
    dry_run = _make("-n", "test-infrastructure")
    assert "infra-suite-pipelines" in dry_run.stdout


def test_an_unknown_suite_fails_before_anything_starts():
    run = _make("test-infrastructure", "SUITE=nope")
    assert run.returncode != 0
    assert "Unknown SUITE='nope'" in run.stdout
    assert "disposable_env.py" not in run.stdout


# ── compose/test-streaming.yaml ──────────────────────────────────────────────

STREAMING = yaml.safe_load(er.STREAMING_COMPOSE_FILE.read_text())


def _streaming_runtime() -> er.ExecutionRuntime:
    environment = de.DisposableEnvironment.checked(
        database="sceneops_test", bucket="sceneops-test", server=SERVER
    )
    return er.ExecutionRuntime(environment, api_port=18000, streaming=True)


def test_the_streaming_services_are_named_apart_from_the_reference_stack():
    assert all(name.startswith("test-") for name in STREAMING["services"])
    assert "test-streaming.yaml" not in (REPO_ROOT / "compose.yaml").read_text()


def test_the_streaming_path_has_its_own_broker_and_volumes():
    services = STREAMING["services"]
    assert "ports" not in services["test-kafka"]
    assert set(STREAMING["volumes"]) == {"test-kafka-data", "test-acquisition-recordings"}
    assert services["test-kafka"]["volumes"] == ["test-kafka-data:/var/lib/kafka/data"]
    assert "test-acquisition-recordings:/recordings" in services["test-capture"]["volumes"]
    assert "test-acquisition-recordings:/recordings:ro" in (
        services["test-recording-publisher"]["volumes"]
    )
    # The reference broker's replay window and timestamp type.
    reference = yaml.safe_load((REPO_ROOT / "compose/streaming.yaml").read_text())
    for name in (
        "KAFKA_LOG_RETENTION_HOURS",
        "KAFKA_LOG_ROLL_HOURS",
        "KAFKA_LOG_RETENTION_BYTES",
        "KAFKA_LOG_MESSAGE_TIMESTAMP_TYPE",
    ):
        assert (
            services["test-kafka"]["environment"][name]
            == reference["services"]["kafka"]["environment"][name]
        ), name


def test_the_static_streaming_configuration_is_isolated():
    """The check `up` applies to the resolved configuration holds for the file
    itself (the runner's root stands in for its interpolation)."""
    services = STREAMING["services"]
    resolved = {
        name: {
            **service,
            "networks": {n: None for n in service.get("networks", ["default"])},
            "environment": {
                k: str(v).replace("${TEST_ARTIFACT_ROOT_URI:?}", "s3://sceneops-test/artifacts")
                for k, v in (service.get("environment") or {}).items()
            },
        }
        for name, service in services.items()
    }
    assert er.streaming_isolation_problems(resolved, "s3://sceneops-test/artifacts") == []
    resolved["test-capture"]["environment"]["SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS"] = (
        "kafka:9092"
    )
    resolved["test-streaming-bridge"]["networks"] = {"streaming": None, "default": None}
    resolved["test-recording-publisher"]["environment"][
        "SCENEOPS_PUBLISHER_ARTIFACT__ROOT_URI"
    ] = "s3://sceneops/artifacts"
    problems = er.streaming_isolation_problems(resolved, "s3://sceneops-test/artifacts")
    assert len(problems) == 3, problems


def test_only_the_broker_starts_with_the_runtime():
    services = STREAMING["services"]
    assert "profiles" not in services["test-kafka"]
    oneshot = {n for n, s in services.items() if s.get("profiles") == [er.ONESHOT_PROFILE]}
    assert oneshot == set(services) - {"test-kafka"}


def test_a_streaming_runtime_hands_its_compose_settings_to_the_streaming_path():
    child = _streaming_runtime().child_environment({})
    assert child["STREAMING_RUNTIME"] == "disposable"
    assert child["TEST_ARTIFACT_ROOT_URI"] == "s3://sceneops-test/artifacts"
    assert child["TEST_DATABASE_URL"].endswith("@postgres:5432/sceneops_test")
    assert child["TEST_API_PORT"] == "18000"
    assert "STREAMING_RUNTIME" not in _runtime().child_environment({})


def test_down_always_covers_the_streaming_services_and_volumes():
    """A killed streaming run leaves its broker and volumes; any later run removes them."""
    for runtime in (_runtime(), _streaming_runtime()):
        command = runtime._compose_command(streaming=True)
        assert str(er.STREAMING_COMPOSE_FILE) in command
    assert str(er.STREAMING_COMPOSE_FILE) not in _runtime()._compose_command()
    assert str(er.STREAMING_COMPOSE_FILE) in _streaming_runtime()._compose_command()


def test_the_streaming_launcher_drives_this_project():
    """tools/baselines/streaming/streaming_lib.sh names the project and files itself."""
    lib = (REPO_ROOT / "tools/baselines/streaming/streaming_lib.sh").read_text()
    assert f"-p {er.PROJECT} " in lib
    assert "compose/test-runtime.yaml" in lib and "compose/test-streaming.yaml" in lib
    assert f"--profile {er.ONESHOT_PROFILE}" in lib
    assert er.STREAMING_KAFKA in er.STREAMING_COMPOSE_FILE.read_text()
