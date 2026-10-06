"""Unit tests of the disposable-environment safety rules (no PostgreSQL / MinIO needed).

The lifecycle itself (create, migrate, drop, rerun after interruption) needs the
real servers and is exercised by `make test-integration` / `make test-recovery`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import disposable_env as de
import require_disposable_environment as plugin

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER = de.Server.from_environ({})


@pytest.mark.parametrize("name", ["sceneops", "postgres", "template1", "sceneops_prod"])
def test_database_names_that_are_not_disposable_are_refused(name):
    with pytest.raises(de.NotDisposableError):
        de.check_disposable_database(name)


@pytest.mark.parametrize(
    "name", ["sceneops_test", "sceneops_test_2", "sceneops_test_ci"]
)
def test_disposable_database_names_are_accepted(name):
    assert de.check_disposable_database(name) == name


def test_database_equal_to_the_configured_reference_is_refused_even_if_it_matches_the_pattern():
    with pytest.raises(de.NotDisposableError):
        de.check_disposable_database("sceneops_test", reference="sceneops_test")


@pytest.mark.parametrize(
    "name", ["sceneops", "sceneops-prod", "other", "sceneops_test"]
)
def test_bucket_names_that_are_not_disposable_are_refused(name):
    with pytest.raises(de.NotDisposableError):
        de.check_disposable_bucket(name)


def test_bucket_equal_to_the_configured_reference_is_refused():
    with pytest.raises(de.NotDisposableError):
        de.check_disposable_bucket("sceneops-test", reference="sceneops-test")


def test_environment_cannot_be_built_for_the_reference_names():
    with pytest.raises(de.NotDisposableError):
        de.DisposableEnvironment.checked(
            database="sceneops", bucket="sceneops-test", server=SERVER
        )
    with pytest.raises(de.NotDisposableError):
        de.DisposableEnvironment.checked(
            database="sceneops_test", bucket="sceneops", server=SERVER
        )


def test_child_environment_points_the_suite_at_the_disposable_names_only():
    env = de.DisposableEnvironment.checked(
        database="sceneops_test", bucket="sceneops-test", server=SERVER
    )
    child = env.child_environment(
        {
            "SCENEOPS_DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/sceneops",
            "MINIO_BUCKET": "sceneops",
            "PATH": "/bin",
        }
    )
    assert de.database_name_of(child["SCENEOPS_DATABASE_URL"]) == "sceneops_test"
    assert child["MINIO_BUCKET"] == "sceneops-test"
    assert child["PATH"] == "/bin"


@pytest.mark.parametrize(
    "environ",
    [
        {"SCENEOPS_DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/sceneops"},
        {"SCENEOPS_DATABASE_URL": "postgresql+asyncpg://u:p@postgres:5432/sceneops"},
        {"MINIO_BUCKET": "sceneops"},
        {
            "SCENEOPS_DATABASE_URL": "postgresql+asyncpg://u:p@localhost/sceneops_test",
            "MINIO_BUCKET": "sceneops",
        },
    ],
)
def test_check_environment_refuses_any_non_disposable_target(environ):
    with pytest.raises(de.NotDisposableError):
        de.check_environment(environ)


def test_check_environment_accepts_the_disposable_target_and_an_unset_one():
    de.check_environment(
        {
            "SCENEOPS_DATABASE_URL": "postgresql+asyncpg://u:p@localhost/sceneops_test",
            "MINIO_BUCKET": "sceneops-test",
        }
    )
    de.check_environment({})


def test_the_cli_refuses_the_reference_database_before_touching_a_server(capsys):
    assert de.main(["drop", "--database", "sceneops"]) == 2
    assert "reference environment" in capsys.readouterr().err
    assert de.main(["create", "--bucket", "sceneops"]) == 2


def test_the_pytest_plugin_aborts_the_session_on_the_reference_database(monkeypatch):
    monkeypatch.setenv(
        "SCENEOPS_DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/sceneops"
    )
    with pytest.raises(pytest.exit.Exception) as raised:
        plugin.pytest_sessionstart(None)
    assert raised.value.returncode == pytest.ExitCode.USAGE_ERROR


def test_the_pytest_plugin_lets_a_disposable_session_start(monkeypatch):
    monkeypatch.setenv(
        "SCENEOPS_DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/sceneops_test"
    )
    monkeypatch.setenv("MINIO_BUCKET", "sceneops-test")
    plugin.pytest_sessionstart(None)


@pytest.mark.parametrize("target", ["test-integration", "test-recovery"])
def test_the_make_targets_run_inside_the_disposable_environment(target):
    """The wiring is part of the contract: both targets go through the runner and
    the guard plugin, and neither builds a database URL of its own."""
    text = "".join(p.read_text() for p in (REPO_ROOT / "makefiles").glob("*.mk"))
    match = re.search(rf"^{target}:.*?(?=^\.PHONY|^[a-z-]+:|\Z)", text, re.S | re.M)
    assert match, target
    recipe = match.group(0)
    assert "$(DISPOSABLE_ENV_RUN)" in recipe
    assert "SCENEOPS_DATABASE_URL" not in recipe
