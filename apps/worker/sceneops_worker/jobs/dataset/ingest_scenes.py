"""IngestScenesJobHandler: canonical SceneManifest ingestion (SceneOps V2
Request 4.6B).

Distinct from ``BuildScenesJobHandler``'s raw-log -> ``BUILD_SCENES`` flow:
this job builds one canonical Scene directly per real source scene,
including ground-truth annotations (nuScenes' own ``sample_annotation``
records) -- no segmentation/sampling stage, no intermediate raw-log
artifact. This is the repository's only source of ground-truth-bearing
scenes (consumed by ``sceneops_worker.evaluation.detection`` and
``sceneops_worker.jobs.scenarios``), which is why it was migrated rather
than removed when its in-process ``nuscenes-devkit`` call was audited
(see ``sceneops_integrations.nuscenes.scene_ingest``'s own docstring for
the full audit).

nuScenes ingestion now runs through the generic execution model exactly
like ``BuildScenesJobHandler``'s raw-log path (Request 4.6/4.6A): this
handler builds an ``IntegrationRequest``, runs it through
``HttpIntegrationExecutor`` against the nuScenes integration HTTP service,
then reads the produced ``SceneManifest`` artifacts back and does its own
DB session / ``ArtifactRecord`` registration / ``DatasetVersion`` summary
update -- unchanged from before this migration. The worker no longer
imports ``nuscenes-devkit`` or ``nuscenes.nuscenes.NuScenes`` directly for
this job.
"""

from __future__ import annotations

from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.datasets.schemas import DatasetType
from sceneops_core.datasets.schemas.records import DatasetVersionRecord
from sceneops_core.jobs.schemas import (
    IngestScenesJobParams,
    IngestScenesJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.scenes.schemas.manifests import SceneManifest
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class IngestScenesJobHandler(JobHandler[IngestScenesJobParams, IngestScenesJobResult]):
    @property
    def job_type(self) -> JobType:
        return JobType.INGEST_SCENES

    @property
    def params_model(self) -> type[IngestScenesJobParams]:
        return IngestScenesJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }

    async def run(
        self,
        request: JobHandlerRequest[IngestScenesJobParams],
    ) -> IngestScenesJobResult:
        job = request.job
        params = request.params
        context = request.context

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        # SceneOps V2 Request 05: DatasetVersion.status no longer tracks Scene
        # workflow progress — auto-create with the default (generic)
        # REGISTERED state if this is the first time we've seen this version.
        version = await context.dataset_store.get_version(
            dataset_id=dataset_id, version=dataset_version
        )
        if version is None:
            version = await context.dataset_store.create_version(
                DatasetVersionRecord(dataset_id=dataset_id, version=dataset_version)
            )

        scene_ids: list[str] = []
        scene_manifest_uris: list[str] = []
        total_samples = 0
        total_frames = 0
        all_channels: set[str] = set()

        scenes = await _ingest_scenes(params=params, context=context, job=job)

        for scene_id, scene_manifest_uri, manifest in scenes:
            scene_ids.append(scene_id)
            scene_manifest_uris.append(scene_manifest_uri)
            total_samples += manifest.sample_count
            total_frames += manifest.frame_count
            all_channels.update(manifest.channels)

            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.SCENE_MANIFEST,
                    uri=scene_manifest_uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE,
                owner_id=scene_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                scene_id=scene_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        channels = sorted(all_channels)

        await context.dataset_store.update_scene_summary(
            dataset_id=dataset_id,
            version=dataset_version,
            scene_count=len(scene_ids),
            sample_count=total_samples,
            frame_count=total_frames,
            channels=channels,
        )

        return IngestScenesJobResult(
            scene_ids=scene_ids,
            scene_manifest_uris=scene_manifest_uris,
            scene_count=len(scene_ids),
            sample_count=total_samples,
            frame_count=total_frames,
            channels=channels,
        )


async def _ingest_scenes(
    *,
    params: IngestScenesJobParams,
    context: WorkerContext,
    job: Any,
) -> list[tuple[str, str, Any]]:
    if params.source_format == DatasetType.NUSCENES:
        return await _ingest_nuscenes_scenes(params=params, context=context, job=job)
    raise ValueError(f"Unsupported source_format: {params.source_format}")


async def _ingest_nuscenes_scenes(
    *,
    params: IngestScenesJobParams,
    context: WorkerContext,
    job: Any,
) -> list[tuple[str, str, Any]]:
    """nuScenes direct-SceneManifest ingest via the generic execution model
    (SceneOps V2 Request 4.6B): build an IntegrationRequest, run it through
    the isolated nuscenes-integration HTTP service, then read each
    produced SceneManifest artifact back into a typed object. The worker
    remains solely responsible for everything downstream of this call
    (DB session, ArtifactRecord registration, lineage, DatasetVersion
    state -- unchanged, in run() above). The service itself stays
    DB-free -- it only produces scene_manifest artifacts and reports their
    URIs/checksums.
    """
    dataset_id = params.dataset_id
    dataset_version = params.dataset_version

    if not params.source_format_version:
        raise ValueError(
            "IngestScenesJobParams.source_format_version is required for "
            "source_format=nuscenes -- refusing to fall back to "
            f"dataset_version={dataset_version!r}"
        )

    # pylint: disable=import-outside-toplevel
    from sceneops_worker.datasets.ingestion.nuscenes_ingestion import (
        build_nuscenes_scene_http_config,
        build_nuscenes_scene_ingest_request,
    )
    from sceneops_worker.integration_execution import HttpIntegrationExecutor

    integration_request = build_nuscenes_scene_ingest_request(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        source_root_uri=params.source_root_uri,
        source_format_version=params.source_format_version,
        source_scene_ids=params.source_scene_ids,
        max_source_scenes=params.max_source_scenes,
    )

    scene_manifest_root_uri = context.scene_artifact_store.scenes_root_uri(
        dataset_id=dataset_id, dataset_version=dataset_version
    )

    config = build_nuscenes_scene_http_config(
        settings=context.settings,
        scene_manifest_root_uri=scene_manifest_root_uri,
    )
    result = await HttpIntegrationExecutor(config).execute(integration_request)

    results: list[tuple[str, str, Any]] = []
    for artifact_ref in result.produced_artifacts.values():
        scene_id = artifact_ref.metadata["scene_id"]
        raw = await context.scene_artifact_store.artifact_store.read_json(
            artifact_ref.uri
        )
        manifest = SceneManifest.model_validate(raw)
        results.append((scene_id, artifact_ref.uri, manifest))

    return results
