"""Read-only Source Registry routes (ADR-015 §5).

Records are created only by ``REGISTER_SOURCE_SNAPSHOT``, submitted through
the Jobs API (``POST /jobs`` with ``{"type": "register_source_snapshot",
"params": {"descriptor_uri": ...}}``) with the ``descriptor_uri`` that
``publish-snapshot`` printed. Every declaration of one content is listed by
``GET /source-snapshots?content_id=...``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import raise_not_found
from app.core.pagination import PaginationDep
from app.core.repositories import SourceRegistryRepositoryDep
from app.domains.sources.schemas import (
    SourceContentDetailResponse,
    SourceSnapshotDetailResponse,
    SourceSnapshotListResponse,
)

source_snapshots_router = APIRouter()
source_contents_router = APIRouter()


@source_snapshots_router.get("", response_model=SourceSnapshotListResponse)
async def list_source_snapshots(
    *,
    repository: SourceRegistryRepositoryDep,
    pagination: PaginationDep,
    content_id: str | None = None,
) -> SourceSnapshotListResponse:
    snapshots = list(
        await repository.list_snapshots(
            content_id=content_id, limit=pagination.limit, offset=pagination.offset
        )
    )
    return SourceSnapshotListResponse(source_snapshots=snapshots, count=len(snapshots))


@source_snapshots_router.get(
    "/{snapshot_id}", response_model=SourceSnapshotDetailResponse
)
async def get_source_snapshot(
    snapshot_id: str, repository: SourceRegistryRepositoryDep
) -> SourceSnapshotDetailResponse:
    snapshot = await repository.get_snapshot(snapshot_id)
    if snapshot is None:
        raise_not_found("SourceSnapshot", snapshot_id)
    return SourceSnapshotDetailResponse(source_snapshot=snapshot)


@source_contents_router.get("/{content_id}", response_model=SourceContentDetailResponse)
async def get_source_content(
    content_id: str, repository: SourceRegistryRepositoryDep
) -> SourceContentDetailResponse:
    content = await repository.get_content(content_id)
    if content is None:
        raise_not_found("SourceContent", content_id)
    return SourceContentDetailResponse(source_content=content)
