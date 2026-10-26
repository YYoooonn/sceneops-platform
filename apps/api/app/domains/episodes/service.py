from __future__ import annotations

import json

from sceneops_core.common.checksums import sha256_checksum
from sceneops_core.episodes.schemas import (
    EpisodeProfileRunRecord,
    EpisodeRevisionRecord,
    EpisodeValidationRunRecord,
    load_canonical_episode_manifest,
)
from sceneops_core.runs.schemas import RunStatus, RunType
from sceneops_db.queries import episode_revision_manifest_source
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.episodes import EpisodeRepository, EpisodeRunRepository
from sceneops_storage import ArtifactStore

from app.core.revisions import RevisionAddress
from app.domains.episodes.quality import build_episode_quality
from app.domains.episodes.schemas import (
    EpisodeDetailResponse,
    EpisodeListResponse,
    EpisodeManifestResponse,
    EpisodeQualityResponse,
)


class EpisodeManifestUnavailableError(RuntimeError):
    """The Episode's pinned manifest bytes are missing or do not match."""


class EpisodeService:
    """Read-only Episode resource service. Units, revisions and members are
    written only by the REGISTER_EPISODES registrar; validation and profiling are triggered
    through pipelines, never here."""

    def __init__(
        self,
        *,
        repository: EpisodeRepository,
        run_repository: EpisodeRunRepository | None = None,
        artifact_repository: ArtifactRepository | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._repository = repository
        self._run_repository = run_repository
        self._artifact_repository = artifact_repository
        self._artifact_store = artifact_store

    async def list_episodes(
        self,
        *,
        robot_run_id: str | None = None,
        content_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> EpisodeListResponse:
        episodes = await self._repository.list(
            robot_run_id=robot_run_id,
            content_id=content_id,
            limit=limit,
            offset=offset,
        )
        return EpisodeListResponse(episodes=episodes, count=len(episodes))

    async def get_episode(self, episode_id: str) -> EpisodeDetailResponse | None:
        episode = await self._repository.get(episode_id)
        if episode is None:
            return None
        return EpisodeDetailResponse(
            episode=episode,
            revisions=await self._repository.list_revisions(episode_id),
        )

    async def get_episode_manifest(
        self, episode_id: str, address: RevisionAddress
    ) -> EpisodeManifestResponse | None:
        """The addressed revision, bytes verified and strictly parsed."""
        assert (
            self._artifact_repository is not None and self._artifact_store is not None
        )
        revision = await self._revision(episode_id, address)
        if revision is None:
            return None
        artifact = await episode_revision_manifest_source(
            artifact_repository=self._artifact_repository, revision=revision
        )
        data = await self._artifact_store.read_bytes(artifact.uri)
        if sha256_checksum(data) != artifact.checksum:
            raise EpisodeManifestUnavailableError(
                f"episode {episode_id} manifest bytes do not match {artifact.checksum}"
            )
        manifest = load_canonical_episode_manifest(data)
        return EpisodeManifestResponse(
            episode_id=episode_id,
            manifest_artifact_id=artifact.artifact_id,
            manifest_checksum=artifact.checksum,
            manifest=json.loads(manifest.to_canonical_bytes()),
        )

    async def get_episode_quality(
        self, episode_id: str, address: RevisionAddress
    ) -> EpisodeQualityResponse | None:
        """Quality of the addressed revision of ``episode_id``; None if no
        such revision is registered (or the DatasetVersion does not pin
        one)."""
        episode = await self._revision(episode_id, address)
        if episode is None:
            return None
        return build_episode_quality(
            episode=episode,
            dataset_id=address.dataset_id,
            dataset_version=address.dataset_version,
            validation_run=await self._latest_revision_run(
                episode, RunType.EPISODE_VALIDATION, EpisodeValidationRunRecord
            ),
            profile_run=await self._latest_revision_run(
                episode, RunType.EPISODE_PROFILE, EpisodeProfileRunRecord
            ),
        )

    async def _revision(
        self, episode_id: str, address: RevisionAddress
    ) -> EpisodeRevisionRecord | None:
        if address.manifest_checksum is not None:
            return await self._repository.get_revision(
                episode_id=episode_id, manifest_checksum=address.manifest_checksum
            )
        assert address.dataset_id and address.dataset_version
        return await self._repository.get_member_revision(
            dataset_id=address.dataset_id,
            dataset_version=address.dataset_version,
            episode_id=episode_id,
        )

    async def _latest_revision_run(self, episode: EpisodeRevisionRecord, run_type, cls):
        """Newest succeeded run of ``run_type`` that assessed exactly this
        revision."""
        if self._run_repository is None:
            return None
        runs = await self._run_repository.list(
            type=run_type,
            status=RunStatus.SUCCEEDED,
            episode_id=episode.episode_id,
            manifest_artifact_id=episode.manifest_artifact_id,
            limit=20,
        )
        for run in runs:
            if isinstance(run, cls) and run.assessed(
                manifest_artifact_id=episode.manifest_artifact_id,
                manifest_checksum=episode.manifest_checksum,
            ):
                return run
        return None
