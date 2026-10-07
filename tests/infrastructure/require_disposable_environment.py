"""Pytest plugin for suites that commit state of their own (`-p require_disposable_environment`).

`make test-integration` and `make test-infrastructure SUITE=recovery` run inside the disposable
environment (`disposable_env.py`). If a session is started with a database or
bucket that is not disposable -- the reference environment's, typically by
exporting SCENEOPS_DATABASE_URL by hand -- it aborts before collecting a test, so
these suites cannot leave rows or objects in the reference environment.
"""

from __future__ import annotations

import os

import pytest

from disposable_env import NotDisposableError, check_environment


def pytest_sessionstart(session: pytest.Session) -> None:
    try:
        check_environment(dict(os.environ))
    except NotDisposableError as exc:
        pytest.exit(
            f"{exc}\nRun this suite through `make test-integration` / "
            "`make test-infrastructure SUITE=recovery`: they create and drop a disposable database and bucket.",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
