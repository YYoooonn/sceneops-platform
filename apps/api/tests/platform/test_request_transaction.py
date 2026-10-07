"""A mutating request's transaction commits before its response is sent.

`DbSessionDep` is a ``scope="function"`` dependency: FastAPI runs its exit code right
after the path operation returns, before the response starts. A client that acts on a
2xx at once (create, then execute / GET) therefore always finds the write committed,
and a commit that fails is an error response, never a failure after a success.

The sessions here record the order of events; the real-PostgreSQL counterpart is
``test_request_transaction_integration.py``.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

import app.core.dependencies as dependencies
from app.core.dependencies import DbSessionDep


class _RecordingSession:
    def __init__(self, events: list[str], *, fail_commit: bool = False) -> None:
        self._events = events
        self._fail_commit = fail_commit

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_exc) -> bool:
        self._events.append("close")
        return False

    async def commit(self) -> None:
        if self._fail_commit:
            self._events.append("commit-failed")
            raise RuntimeError("commit failed")
        self._events.append("commit")

    async def rollback(self) -> None:
        self._events.append("rollback")


async def _post(app: FastAPI, events: list[str]) -> int | None:
    """Drives one request through the ASGI app; returns the response status."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/write",
        "raw_path": b"/write",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 5000),
    }
    status: list[int] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            events.append("response-start")
            status.append(message["status"])

    try:
        await app(scope, receive, send)
    except RuntimeError:
        # An unhandled server error is re-raised by the ASGI stack after the 500
        # response was sent; the test asserts on what the client was sent.
        pass
    return status[0] if status else None


def _app(monkeypatch, events: list[str], *, fail_commit: bool = False) -> FastAPI:
    monkeypatch.setattr(
        dependencies,
        "get_async_sessionmaker",
        lambda: lambda: _RecordingSession(events, fail_commit=fail_commit),
    )
    return FastAPI()


async def test_the_session_commits_before_the_response_is_sent(monkeypatch):
    events: list[str] = []
    app = _app(monkeypatch, events)

    @app.post("/write")
    async def write(session: DbSessionDep) -> dict:
        events.append("handler")
        return {"ok": True}

    assert await _post(app, events) == 200

    assert events[:2] == ["handler", "commit"], events
    assert events.index("commit") < events.index("response-start"), events


async def test_commit_precedes_the_response_through_nested_dependencies(monkeypatch):
    """Routes reach the session through repository and service dependencies
    (app.core.repositories), not directly."""
    events: list[str] = []
    app = _app(monkeypatch, events)

    def repository(session: DbSessionDep) -> object:
        return object()

    def service(repo: object = Depends(repository)) -> object:
        return object()

    @app.post("/write")
    async def write(svc: object = Depends(service)) -> dict:
        events.append("handler")
        return {"ok": True}

    assert await _post(app, events) == 200

    assert events.index("commit") < events.index("response-start"), events


async def test_a_failed_commit_is_an_error_response_not_a_success(monkeypatch):
    events: list[str] = []
    app = _app(monkeypatch, events, fail_commit=True)

    @app.post("/write")
    async def write(session: DbSessionDep) -> dict:
        return {"ok": True}

    assert await _post(app, events) == 500

    assert events.index("commit-failed") < events.index("response-start"), events
    assert "rollback" in events, events


async def test_an_error_response_rolls_the_request_back(monkeypatch):
    events: list[str] = []
    app = _app(monkeypatch, events)

    @app.post("/write")
    async def write(session: DbSessionDep) -> dict:
        raise HTTPException(status_code=404, detail="missing")

    assert await _post(app, events) == 404

    assert "commit" not in events, events
    assert "rollback" in events, events


async def test_the_session_is_closed_before_the_response_is_sent(monkeypatch):
    events: list[str] = []
    app = _app(monkeypatch, events)

    @app.post("/write")
    async def write(session: DbSessionDep) -> dict:
        return {"ok": True}

    await _post(app, events)

    assert events.index("close") < events.index("response-start"), events
