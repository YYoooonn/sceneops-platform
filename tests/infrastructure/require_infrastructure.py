"""Pytest plugin for the real-infrastructure commands (`-p require_infrastructure`).

The infrastructure suites skip a test when PostgreSQL, MinIO, the API or Docker is
unreachable, which is right for a developer running one file but wrong for a command
whose whole purpose is to prove real-infrastructure behavior: a stopped stack must
not turn into a green run. Under this plugin any skipped test fails the session, so
neither "everything skipped" nor "the MinIO half skipped" is reported as success.

Opt-in modules that are not part of the command's scope are excluded with
`--ignore`, not skipped.
"""

from __future__ import annotations

import pytest

_skipped: list[tuple[str, str]] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped:
        reason = (
            report.longrepr[2]
            if isinstance(report.longrepr, tuple)
            else str(report.longrepr)
        )
        _skipped.append((report.nodeid, reason.removeprefix("Skipped: ")))


def pytest_terminal_summary(terminalreporter) -> None:
    if not _skipped:
        return
    terminalreporter.section("real-infrastructure run skipped tests", red=True)
    for nodeid, reason in _skipped:
        terminalreporter.write_line(f"{nodeid}: {reason}")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _skipped and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
