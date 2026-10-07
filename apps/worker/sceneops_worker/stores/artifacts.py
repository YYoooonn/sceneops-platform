from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_core.artifacts.schemas import ArtifactKind, ArtifactRecord, ArtifactRef
from sceneops_db.postgres import PostgresArtifactRefRepository


class ArtifactRecordConflictError(RuntimeError):
    """A deterministic artifact id is already registered with different content."""


class ArtifactRecordStore:
    """DB-backed store for artifact ref records.

    Named ArtifactRecordStore to avoid collision with sceneops_storage.ArtifactStore.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = PostgresArtifactRefRepository(session)

    async def create(
        self,
        *,
        artifact_id: str,
        ref: ArtifactRef,
        backend: str | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        scenario_set_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
    ) -> ArtifactRecord:
        return await self._repo.create(
            artifact_id=artifact_id,
            ref=ref,
            backend=backend,
            owner_type=owner_type,
            owner_id=owner_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
            scenario_set_id=scenario_set_id,
            run_id=run_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
        )

    async def create_if_absent(
        self,
        *,
        artifact_id: str,
        ref: ArtifactRef,
        **owner: str | None,
    ) -> tuple[ArtifactRecord, bool]:
        """The record of a deterministic id: inserted, or the one that exists
        (``(record, created)``). Concurrent Jobs writing one id converge on one
        row instead of failing; comparing an existing record's content is the
        caller's."""
        return await self._repo.create_if_absent(
            artifact_id=artifact_id, ref=ref, **owner
        )

    async def register(
        self,
        *,
        artifact_id: str,
        ref: ArtifactRef,
        **owner: str | None,
    ) -> tuple[ArtifactRecord, bool]:
        """Idempotent registration of a deterministic ArtifactRecord.

        Returns ``(record, created)``. An existing record with the same
        kind, location, checksum and size is the retry of an earlier
        attempt, or the write of a concurrent Job, and is reused unchanged;
        one that differs is a conflicting duplicate and fails loudly
        (ArtifactRecordConflictError)."""
        existing, created = await self.create_if_absent(
            artifact_id=artifact_id, ref=ref, **owner
        )
        if created:
            return existing, True
        if (
            existing.kind != ref.kind.value
            or existing.uri != ref.uri
            or existing.checksum != ref.checksum
            or existing.size_bytes != ref.size_bytes
        ):
            raise ArtifactRecordConflictError(
                f"artifact {artifact_id} is already registered as "
                f"{existing.kind} {existing.uri} {existing.checksum}; "
                f"refusing to re-register it as {ref.kind.value} {ref.uri} {ref.checksum}"
            )
        return existing, False

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        return await self._repo.get(artifact_id)

    async def get_many(self, artifact_ids: list[str]) -> dict[str, ArtifactRecord]:
        return await self._repo.get_many(artifact_ids)

    async def list(
        self,
        *,
        kind: ArtifactKind | None = None,
        owner_type: str | None = None,
        owner_id: str | None = None,
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        scene_id: str | None = None,
        scenario_set_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArtifactRecord]:
        return await self._repo.list(
            kind=kind,
            owner_type=owner_type,
            owner_id=owner_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            scene_id=scene_id,
            scenario_set_id=scenario_set_id,
            run_id=run_id,
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            limit=limit,
            offset=offset,
        )
