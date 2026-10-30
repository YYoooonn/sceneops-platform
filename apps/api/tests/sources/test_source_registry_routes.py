"""The Source Registry API is read-only: records are created only by
REGISTER_SOURCE_SNAPSHOT, which is submitted through the Jobs API. Route
functions are called directly with an in-memory repository, matching this
suite's no-TestClient convention."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.sources.router import (
    get_source_content,
    get_source_snapshot,
    list_source_snapshots,
)
from sceneops_core.jobs.schemas import JobType, parse_job_params
from sceneops_core.sources import SourceContentRecord, SourceSnapshotRecord

_CONTENT = SourceContentRecord(
    content_id="content-" + "a" * 32,
    format="lerobot",
    content_checksum="sha256:" + "1" * 64,
    inventory_artifact_id="art-sourceinventory-content-" + "a" * 32,
    file_count=7,
    total_bytes=7_684_382,
)


def _snapshot(suffix: str, revision: str) -> SourceSnapshotRecord:
    return SourceSnapshotRecord(
        snapshot_id="snapshot-" + suffix * 32,
        content_id=_CONTENT.content_id,
        format="lerobot",
        upstream="lerobot/pusht",
        upstream_revision=revision,
        selection_checksum="sha256:" + "2" * 64,
        descriptor_artifact_id="art-sourcedescriptor-snapshot-" + suffix * 32,
        descriptor_checksum="sha256:" + suffix * 64,
    )


class _Registry:
    def __init__(self) -> None:
        self.snapshots = [_snapshot("b", "rev-1"), _snapshot("c", "rev-2")]

    async def get_content(self, content_id):
        return _CONTENT if content_id == _CONTENT.content_id else None

    async def get_snapshot(self, snapshot_id):
        return next((s for s in self.snapshots if s.snapshot_id == snapshot_id), None)

    async def list_snapshots(self, *, content_id=None, limit=100, offset=0):
        rows = [s for s in self.snapshots if content_id in (None, s.content_id)]
        return rows[offset : offset + limit]


class _Page:
    limit, offset = 100, 0


async def test_every_declaration_of_a_content_is_listed() -> None:
    response = await list_source_snapshots(
        repository=_Registry(), pagination=_Page(), content_id=_CONTENT.content_id
    )
    assert response.count == 2
    assert {s.upstream_revision for s in response.source_snapshots} == {
        "rev-1",
        "rev-2",
    }


async def test_detail_routes_return_records_and_404_otherwise() -> None:
    registry = _Registry()
    snapshot = registry.snapshots[0]

    detail = await get_source_snapshot(snapshot.snapshot_id, registry)
    assert detail.source_snapshot == snapshot
    content = await get_source_content(_CONTENT.content_id, registry)
    assert content.source_content == _CONTENT
    with pytest.raises(HTTPException) as missing:
        await get_source_content("content-" + "f" * 32, registry)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException):
        await get_source_snapshot("snapshot-" + "f" * 32, registry)


def test_routes_are_read_only() -> None:
    from app.main import app

    routes = {
        (method, route.path)
        for route in app.routes
        if "/source-" in getattr(route, "path", "")
        for method in route.methods
    }
    assert routes == {
        ("GET", "/api/v1/source-snapshots"),
        ("GET", "/api/v1/source-snapshots/{snapshot_id}"),
        ("GET", "/api/v1/source-contents/{content_id}"),
    }


def test_registration_is_a_jobs_api_job() -> None:
    params = parse_job_params(
        JobType.REGISTER_SOURCE_SNAPSHOT,
        {"descriptor_uri": "s3://b/artifacts/sources/snapshots/x/descriptor.json"},
    )
    assert params.descriptor_uri.endswith("descriptor.json")
    with pytest.raises(ValueError):
        parse_job_params(JobType.REGISTER_SOURCE_SNAPSHOT, {"descriptor_uri": ""})
