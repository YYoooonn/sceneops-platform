"""BUILD_SNAPSHOT_SCENES: the producer task of SNAPSHOT_SCENE_BUILDING
(ADR-015 §4, §6.7; M3.0 D2, D7).

    snapshot_id
      -> the registered SourceSnapshot -> its content_id, once
                                              (execution provenance, MS-14)
      -> the registered SourceContent, its inventory ArtifactRecord and the
         stored inventory pinned by content_checksum
      -> the Scene builder of the content's registered format
      -> StoredContentFiles               the files that builder's reader reads,
                                              each verified against the inventory
      -> plan_*_scenes()                  complete SceneManifest set; no payload
      -> manifests, write-once            {canonical_root}/scenes/{scene_id}/manifest-<sha256>.json
      -> SCENE_MANIFEST ArtifactRecords   deterministic ids; reuse or conflict
      -> commit; result.manifest_artifact_ids -> REGISTER_SCENES

The build reads the content only, never the snapshot's descriptor, so every
snapshot of the same bytes builds the same manifests. The snapshot this
build used is recorded in its result; the manifest ArtifactRecords keep the
Job that first produced them. It writes no Scene unit or revision, no payload
object (preserved files are referenced, not copied) and no DatasetVersion.

Failure and retry are those of the recording build: keys and artifact ids
are deterministic, so a retry converges and anything that differs fails.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    BuildSnapshotScenesJobParams,
    BuildSnapshotScenesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.provenance import PinnedContent
from sceneops_core.sources import SourceContentInventory
from sceneops_scenes.nuscenes_builder import plan_nuscenes_scenes
from sceneops_scenes.plan import SceneBuildPlan
from sceneops_sources import ContentFileError, StoredContentFiles
from sceneops_sources.nuscenes import NuScenesFormatError, nuscenes_read_paths

from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.scenes.publication import publish_scene_manifests
from sceneops_worker.sources.content import resolve_registered_content


class SnapshotSceneBuildError(RuntimeError):
    """The snapshot cannot be built: it is not registered, its content's
    format has no Scene builder, or its files cannot be provided."""


@dataclass(frozen=True)
class _SceneBuilder:
    # The content files the builder's reader reads.
    read_paths: Callable[[SourceContentInventory], list[str]]
    plan: Callable[..., SceneBuildPlan]


# One Scene builder per source format (ADR-015 §6.7), selected by the format
# the content was registered under.
SNAPSHOT_SCENE_BUILDERS: dict[str, _SceneBuilder] = {
    "nuscenes": _SceneBuilder(
        read_paths=nuscenes_read_paths, plan=plan_nuscenes_scenes
    ),
}


class BuildSnapshotScenesJobHandler(
    JobHandler[BuildSnapshotScenesJobParams, BuildSnapshotScenesJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_SNAPSHOT_SCENES

    @property
    def params_model(self) -> type[BuildSnapshotScenesJobParams]:
        return BuildSnapshotScenesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return dict(inputs.params)

    async def run(
        self, request: JobHandlerRequest[BuildSnapshotScenesJobParams]
    ) -> BuildSnapshotScenesJobResult:
        params = request.params
        context = request.context
        snapshot = await context.source_store.get_snapshot(params.snapshot_id)
        if snapshot is None:
            raise SnapshotSceneBuildError(
                f"SourceSnapshot is not registered: {params.snapshot_id}"
            )
        content = await resolve_registered_content(context, snapshot.content_id)
        builder = SNAPSHOT_SCENE_BUILDERS.get(content.record.format)
        if builder is None:
            raise SnapshotSceneBuildError(
                f"SourceContent {content.record.content_id} is registered as "
                f"{content.record.format!r}, which has no Scene builder"
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
            except (ContentFileError, NuScenesFormatError) as exc:
                raise SnapshotSceneBuildError(str(exc)) from exc
            plan = await asyncio.to_thread(
                builder.plan,
                pinned=pinned,
                files=files,
                config=params.build_config,
                unit_keys=params.unit_keys,
            )

        manifest_artifact_ids, created = await publish_scene_manifests(
            context,
            plan,
            job_id=request.job.job_id,
            pipeline_run_id=request.job.pipeline_run_id,
        )
        await context.commit()

        return BuildSnapshotScenesJobResult(
            snapshot_id=snapshot.snapshot_id,
            content_id=content.record.content_id,
            content_checksum=content.record.content_checksum,
            producer_fingerprint=plan.producer.producer_fingerprint,
            unit_keys=[s.unit_key for s in plan.scenes],
            manifest_artifact_ids=manifest_artifact_ids,
            created_manifest_count=created,
            scene_count=len(plan.scenes),
            observation_count=plan.observation_count,
            pose_count=plan.pose_count,
            keyframe_count=plan.keyframe_count,
            channels=sorted(params.build_config.channels),
        )


__all__ = [
    "SNAPSHOT_SCENE_BUILDERS",
    "BuildSnapshotScenesJobHandler",
    "SnapshotSceneBuildError",
]
