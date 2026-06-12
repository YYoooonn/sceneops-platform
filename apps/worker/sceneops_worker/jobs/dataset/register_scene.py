from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import generate_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    JobType,
    RegisterSceneJobParams,
    RegisterSceneJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.scenes.schemas import SceneManifest, SceneStatus
from sceneops_core.scenes.schemas.records import SceneRecord
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest


class RegisterSceneJobHandler(
    JobHandler[RegisterSceneJobParams, RegisterSceneJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.REGISTER_SCENE

    @property
    def params_model(self) -> type[RegisterSceneJobParams]:
        return RegisterSceneJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        # Resolve scene_manifest_uris from upstream refs first, then fall back to params
        scene_manifest_uris = inputs.refs.get("scene_manifest_uris") or []
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
            "scene_manifest_uris": scene_manifest_uris,
        }

    async def run(
        self,
        request: JobHandlerRequest[RegisterSceneJobParams],
    ) -> RegisterSceneJobResult:
        params = request.params
        context = request.context
        job = request.job

        dataset_id = params.dataset_id
        dataset_version = params.dataset_version

        # Resolve scene_manifest_uris: bulk list takes precedence, then singular
        uris: list[str] = list(params.scene_manifest_uris)
        if not uris and params.scene_manifest_uri:
            uris = [params.scene_manifest_uri]

        registered_ids: list[str] = []
        registered_uris: list[str] = []

        for uri in uris:
            manifest = await context.scene_artifact_store.load_scene_manifest(uri)
            if manifest is None:
                continue

            scene_id = manifest.scene_id
            ds_id = dataset_id or manifest.dataset_id
            ds_version = dataset_version or manifest.dataset_version

            record = _build_scene_record_from_manifest(
                scene_id=scene_id,
                dataset_id=ds_id,
                dataset_version=ds_version,
                manifest_uri=uri,
                manifest=manifest,
                params=params,
            )

            existing = await context.scene_store.get(scene_id)

            if existing is not None and not params.replace_existing:
                registered_ids.append(scene_id)
                registered_uris.append(uri)
                continue

            await context.scene_store.upsert(record)

            # Register the scene manifest as an artifact record so it is
            # discoverable via GET /scenes/{scene_id}/artifacts.
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(
                    kind=ArtifactKind.SCENE_MANIFEST,
                    uri=uri,
                    media_type="application/json",
                ),
                owner_type=ArtifactOwnerType.SCENE,
                owner_id=scene_id,
                scene_id=scene_id,
                dataset_id=ds_id,
                dataset_version=ds_version,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

            registered_ids.append(scene_id)
            registered_uris.append(uri)

        await context.commit()

        registered_count = len(registered_ids)

        return RegisterSceneJobResult(
            scene_id=registered_ids[0] if len(registered_ids) == 1 else None,
            scene_manifest_uri=registered_uris[0]
            if len(registered_uris) == 1
            else None,
            scene_ids=registered_ids,
            scene_manifest_uris=registered_uris,
            registered_scene_count=registered_count,
            registered=registered_count > 0,
        )


def _build_scene_record_from_manifest(
    *,
    scene_id: str,
    dataset_id: str | None,
    dataset_version: str | None,
    manifest_uri: str,
    manifest: SceneManifest,
    params: RegisterSceneJobParams,
) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        status=SceneStatus.BUILT,
        origin_type=params.origin_type,
        generation_method=params.generation_method,
        scene_manifest_uri=manifest_uri,
        sample_count=manifest.sample_count,
        frame_count=manifest.frame_count,
        annotation_count=manifest.annotation_count,
        channels=manifest.channels,
        has_ground_truth=manifest.has_ground_truth,
        ground_truth_source=manifest.ground_truth_source,
        metadata=manifest.metadata,
    )
