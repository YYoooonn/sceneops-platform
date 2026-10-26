"""BUILD_SNAPSHOT_EPISODES: the producer task of SNAPSHOT_EPISODE_BUILDING
(ADR-015 §4, §6.7; M3.0 D2, D7).

    snapshot_id
      -> the registered SourceSnapshot -> its content_id, once
                                              (execution provenance, MS-14)
      -> the registered SourceContent, its inventory ArtifactRecord and the
         stored inventory pinned by content_checksum
      -> the Episode builder of the content's registered format
      -> StoredContentFiles               the files that builder's reader reads,
                                              each verified against the inventory
      -> plan_*_episodes()                complete EpisodeManifest set; no payload
      -> manifests, write-once            {canonical_root}/episodes/{episode_id}/manifest-<sha256>.json
      -> EPISODE_MANIFEST ArtifactRecords deterministic ids; reuse or conflict
      -> commit; result.manifest_artifact_ids -> REGISTER_EPISODES

The build reads the content only, never the snapshot's descriptor, so every
snapshot of the same bytes builds the same manifests. The snapshot this
build used is recorded in its result; the manifest ArtifactRecords keep the
Job that first produced them. It writes no Episode unit or revision, no
payload object (preserved files are referenced, not copied) and no
DatasetVersion.

Failure and retry are those of the recording build: keys and artifact ids
are deterministic, so a retry converges and anything that differs fails.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sceneops_core.common.schemas import JsonDict
from sceneops_core.episodes.recording_build import EpisodeStreamRole
from sceneops_core.jobs.schemas import (
    BuildSnapshotEpisodesJobParams,
    BuildSnapshotEpisodesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.provenance import PinnedContent
from sceneops_core.sources import SourceContentInventory
from sceneops_episodes.lerobot_builder import plan_lerobot_episodes
from sceneops_episodes.plan import EpisodeBuildPlan
from sceneops_sources import ContentFileError, StoredContentFiles
from sceneops_sources.lerobot import lerobot_read_paths

from sceneops_worker.episodes.publication import publish_episode_manifests
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.sources.content import resolve_registered_content


class SnapshotEpisodeBuildError(RuntimeError):
    """The snapshot cannot be built: it is not registered, its content's
    format has no Episode builder, or its files cannot be provided."""


@dataclass(frozen=True)
class _EpisodeBuilder:
    # The content files the builder's reader reads.
    read_paths: Callable[[SourceContentInventory], list[str]]
    plan: Callable[..., EpisodeBuildPlan]


# One Episode builder per source format (ADR-015 §6.7), selected by the
# format the content was registered under.
SNAPSHOT_EPISODE_BUILDERS: dict[str, _EpisodeBuilder] = {
    "lerobot": _EpisodeBuilder(
        read_paths=lerobot_read_paths, plan=plan_lerobot_episodes
    ),
}


def _plan(
    builder: _EpisodeBuilder,
    *,
    pinned: PinnedContent,
    files: StoredContentFiles,
    params: BuildSnapshotEpisodesJobParams,
    unit_keys: Sequence[str] | None,
) -> EpisodeBuildPlan:
    return builder.plan(
        pinned=pinned, files=files, config=params.build_config, unit_keys=unit_keys
    )


class BuildSnapshotEpisodesJobHandler(
    JobHandler[BuildSnapshotEpisodesJobParams, BuildSnapshotEpisodesJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_SNAPSHOT_EPISODES

    @property
    def params_model(self) -> type[BuildSnapshotEpisodesJobParams]:
        return BuildSnapshotEpisodesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return dict(inputs.params)

    async def run(
        self, request: JobHandlerRequest[BuildSnapshotEpisodesJobParams]
    ) -> BuildSnapshotEpisodesJobResult:
        params = request.params
        context = request.context
        snapshot = await context.source_store.get_snapshot(params.snapshot_id)
        if snapshot is None:
            raise SnapshotEpisodeBuildError(
                f"SourceSnapshot is not registered: {params.snapshot_id}"
            )
        content = await resolve_registered_content(context, snapshot.content_id)
        builder = SNAPSHOT_EPISODE_BUILDERS.get(content.record.format)
        if builder is None:
            raise SnapshotEpisodeBuildError(
                f"SourceContent {content.record.content_id} is registered as "
                f"{content.record.format!r}, which has no Episode builder"
            )
        pinned = PinnedContent(inventory=content.inventory)
        with tempfile.TemporaryDirectory(prefix="sceneops-content-") as workdir:
            try:
                files = await StoredContentFiles.materialize(
                    store=context.artifact_store,
                    inventory_uri=content.inventory_uri,
                    content_checksum=content.record.content_checksum,
                    workdir=Path(workdir),
                    paths=builder.read_paths(content.inventory),
                )
            except ContentFileError as exc:
                raise SnapshotEpisodeBuildError(str(exc)) from exc
            plan = await asyncio.to_thread(
                _plan,
                builder,
                pinned=pinned,
                files=files,
                params=params,
                unit_keys=params.unit_keys,
            )

        manifest_artifact_ids, created = await publish_episode_manifests(
            context,
            plan,
            job_id=request.job.job_id,
            pipeline_run_id=request.job.pipeline_run_id,
        )
        await context.commit()

        return BuildSnapshotEpisodesJobResult(
            snapshot_id=snapshot.snapshot_id,
            content_id=content.record.content_id,
            content_checksum=content.record.content_checksum,
            producer_fingerprint=plan.producer.producer_fingerprint,
            unit_keys=[e.unit_key for e in plan.episodes],
            manifest_artifact_ids=manifest_artifact_ids,
            created_manifest_count=created,
            episode_count=len(plan.episodes),
            observation_count=plan.count(EpisodeStreamRole.OBSERVATION),
            state_count=plan.count(EpisodeStreamRole.STATE),
            action_count=plan.count(EpisodeStreamRole.ACTION),
            event_count=plan.count(EpisodeStreamRole.EVENT),
            transition_count=plan.count(EpisodeStreamRole.TRANSITION),
            features=sorted(f.feature for f in params.build_config.features),
        )


__all__ = [
    "SNAPSHOT_EPISODE_BUILDERS",
    "BuildSnapshotEpisodesJobHandler",
    "SnapshotEpisodeBuildError",
]
