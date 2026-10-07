"""The request transaction against real PostgreSQL: a success response is sent only
after its write is committed and visible to another connection.

Runs in the disposable database of `make test-integration`; each test commits one
uniquely-identified Dataset there.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.core.dependencies import DbSessionDep
from app.main import create_app
from sceneops_core.datasets.schemas.records import DatasetRecord
from sceneops_db.postgres.datasets import PostgresDatasetRepository
from sceneops_db.session import (
    dispose_async_engine,
    get_async_engine,
    get_async_sessionmaker,
    reset_async_engine_cache,
)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_database_connection():
    if not os.environ.get("SCENEOPS_DATABASE_URL"):
        pytest.skip(
            "SCENEOPS_DATABASE_URL not set -- run via `make test-integration` "
            "against a running `make local-up` stack."
        )

    reset_async_engine_cache()
    try:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - report as a skip, not a failure
        pytest.skip(f"Postgres not reachable at SCENEOPS_DATABASE_URL: {exc}")

    yield

    await dispose_async_engine()


async def _dataset_visible(dataset_id: str) -> bool:
    """Read through a connection of its own, as a client's next request would."""
    async with get_async_sessionmaker()() as session:
        return await PostgresDatasetRepository(session).get(dataset_id) is not None


async def _request(app: FastAPI, method: str, path: str, body: dict | None, on_start):
    payload = json.dumps(body or {}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "server": ("testserver", 80),
        "client": ("testclient", 5000),
    }
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    status: list[int] = []

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
            await on_start()

    await app(scope, receive, send)
    return status[0]


async def test_a_created_dataset_is_committed_when_the_response_starts():
    dataset_id = f"txn-{uuid.uuid4().hex[:10]}"
    visible_at_response_start: list[bool] = []

    async def on_start() -> None:
        visible_at_response_start.append(await _dataset_visible(dataset_id))

    status = await _request(
        create_app(),
        "POST",
        "/api/v1/datasets",
        {"dataset_id": dataset_id},
        on_start,
    )

    assert status == 201
    assert visible_at_response_start == [True]


async def test_an_error_response_leaves_no_write_behind():
    dataset_id = f"txn-{uuid.uuid4().hex[:10]}"
    app = FastAPI()

    @app.post("/write")
    async def write(session: DbSessionDep) -> dict:
        await PostgresDatasetRepository(session).create(
            DatasetRecord(dataset_id=dataset_id)
        )
        await session.flush()
        raise HTTPException(status_code=409, detail="conflict")

    async def on_start() -> None:
        pass

    assert await _request(app, "POST", "/write", None, on_start) == 409
    assert not await _dataset_visible(dataset_id)
