"""The reconciler's dispatch is bounded on a broker that accepts connections but
never answers. ``send_task`` subscribes to the task's result channel before it
publishes, so the bound must hold for the Redis result backend too; these tests
read the values the backend actually uses, not the configuration keys set.

The behaviour on a real paused Redis is
``tests/infrastructure/test_acquisition_recovery.py::test_an_unresponsive_broker_does_not_hang_a_pass``.
"""

from __future__ import annotations

import pytest
from sceneops_core.config import CelerySettings, ExecutionSettings
from sceneops_execution.executions.client import create_celery_client
from sceneops_worker.cli.acquisition import reconcile
from sceneops_worker.cli.acquisition.reconcile import (
    _BROKER_TIMEOUT_SECONDS,
    build_registration_submitter,
)
from sceneops_worker.config import WorkerSettings

# Unreachable on purpose: building the submitter opens no connection.
_BROKER = "redis://127.0.0.1:1/14"
_BACKEND = "redis://127.0.0.1:1/15"


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    # The submitter only stores its session factory; no session is opened here.
    monkeypatch.setattr(reconcile, "get_async_sessionmaker", lambda: None)


def _reconciler_app():
    settings = WorkerSettings(
        execution=ExecutionSettings(
            celery=CelerySettings(broker_url=_BROKER, result_backend=_BACKEND)
        )
    )
    build_registration_submitter(settings)
    # The client is cached per (broker, backend): this is the app it configured.
    return create_celery_client(broker_url=_BROKER, result_backend=_BACKEND)


def test_the_result_backend_sockets_are_bounded():
    backend = _reconciler_app().backend
    assert backend.connparams["socket_timeout"] == _BROKER_TIMEOUT_SECONDS
    assert backend.connparams["socket_connect_timeout"] == _BROKER_TIMEOUT_SECONDS


def test_the_result_backend_reconnects_at_most_once():
    assert _reconciler_app().backend.retry_policy["max_retries"] == 1


def test_the_broker_sockets_are_bounded():
    conf = _reconciler_app().conf
    assert conf.broker_connection_timeout == _BROKER_TIMEOUT_SECONDS
    assert conf.broker_transport_options == {
        "socket_connect_timeout": _BROKER_TIMEOUT_SECONDS,
        "socket_timeout": _BROKER_TIMEOUT_SECONDS,
    }
