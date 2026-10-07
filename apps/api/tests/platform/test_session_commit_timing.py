"""Known limitation, recorded as an expected failure: a control-plane write is committed
after its response has started, not before.

`DbSessionDep` commits when the request completes, and FastAPI runs the exit code of a
`yield` dependency after the response has started (`Depends(..., scope="function")`
would run it right after the path operation returns). A client that acts on a response
at once (POST /pipelines/runs, then POST /pipelines/runs/{id}/execute, which reads
through a session of its own) can therefore find the first request's write not yet
committed and get a 404. The ordering below is deterministic; no HTTP-level failure
could be provoked against a real runtime (about 1,500 create-then-execute pairs, serial
and concurrent, with background write load, none failed), so the observed 404 has not
been attributed to it. When the commit is moved before the response this test passes
and the marker must be removed.
"""

import pytest
from fastapi import FastAPI

import sceneops_db.session as db_session
from app.core.dependencies import DbSessionDep


class _RecordingSession:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def commit(self) -> None:
        self._events.append("commit")

    async def rollback(self) -> None:
        self._events.append("rollback")

    async def close(self) -> None:
        self._events.append("close")


async def _post(app: FastAPI, events: list[str]) -> None:
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

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            events.append("response-start")

    await app(scope, receive, send)


@pytest.mark.xfail(
    strict=True, reason="the session commits after the response has started"
)
async def test_the_session_commits_before_the_response_is_sent(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        db_session, "get_async_sessionmaker", lambda: lambda: _RecordingSession(events)
    )
    app = FastAPI()

    @app.post("/write")
    async def write(session: DbSessionDep) -> dict:
        events.append("handler")
        return {"ok": True}

    await _post(app, events)

    assert events[:2] == ["handler", "commit"], events
    assert events.index("commit") < events.index("response-start"), events
