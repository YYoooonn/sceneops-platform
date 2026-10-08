"""BUILD_SCENE_SAMPLE_VIEWS: registered Scenes + an explicit policy + pinned
label set revisions -> one SceneSampleView revision per Scene
(ADR-007 §33.3).

The view is derived data: it never changes a Scene, and every input is
pinned (the Scene manifest revision, the label set revisions, the policy).
Each Scene is read at the revision its record points to when the job runs
and the view records that revision. A Scene that cannot produce samples
under the policy is skipped *with a reason*, never silently.
"""

from __future__ import annotations

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.derived_ids import sample_view_artifact_id
from sceneops_core.common.schemas import JsonDict
from sceneops_core.jobs.schemas import (
    BuildSceneSampleViewsJobParams,
    BuildSceneSampleViewsJobResult,
    JobType,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.sample_views import (
    SampleViewRef,
    SceneLacksAnchorChannelError,
    SceneRevisionRef,
    build_scene_sample_view,
)
from sceneops_core.scenes.schemas import SceneRecord

from sceneops_derived import DerivedManifestIntegrityError
from sceneops_worker.derived.resolution import resolve_label_set
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, JobHandlerRequest
from sceneops_worker.scenes.resolver import (
    list_dataset_version_scenes,
    resolve_registered_scene,
)


class BuildSceneSampleViewsJobHandler(
    JobHandler[BuildSceneSampleViewsJobParams, BuildSceneSampleViewsJobResult]
):
    @property
    def job_type(self) -> JobType:
        return JobType.BUILD_SCENE_SAMPLE_VIEWS

    @property
    def params_model(self) -> type[BuildSceneSampleViewsJobParams]:
        return BuildSceneSampleViewsJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> JsonDict:
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }

    async def run(
        self, request: JobHandlerRequest[BuildSceneSampleViewsJobParams]
    ) -> BuildSceneSampleViewsJobResult:
        params = request.params
        context = request.context
        job = request.job

        label_sets = [
            (ref, await resolve_label_set(context, ref)) for ref in params.label_sets
        ]
        for ref, manifest in label_sets:
            if manifest.label_set_id != ref.label_set_id:
                raise DerivedManifestIntegrityError(
                    f"label set {ref.manifest_artifact_id} holds "
                    f"{manifest.label_set_id!r}, pinned as {ref.label_set_id!r}"
                )

        records = await self._scenes(context, params)

        views: list[SampleViewRef] = []
        skipped: list[JsonDict] = []
        sample_count = dropped = created = reused = 0
        for record in records:
            resolved = await resolve_registered_scene(context, record)
            scene_ref = SceneRevisionRef(
                scene_id=record.scene_id,
                manifest_artifact_id=record.manifest_artifact_id,
                manifest_checksum=record.manifest_checksum,
            )
            try:
                view = build_scene_sample_view(
                    scene=scene_ref,
                    manifest=resolved.manifest,
                    policy=params.policy,
                    label_sets=label_sets,
                )
            except SceneLacksAnchorChannelError:
                skipped.append(
                    {
                        "scene_id": record.scene_id,
                        "reason": "scene_lacks_anchor_channel",
                    }
                )
                continue
            if not view.samples:
                skipped.append({"scene_id": record.scene_id, "reason": "no_samples"})
                dropped += len(view.dropped)
                continue

            data = view.to_canonical_bytes()
            checksum = view.checksum()
            uri = context.derived_store.sample_view_uri(
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                scene_id=record.scene_id,
                checksum=checksum,
            )
            published = await context.derived_store.publish(uri=uri, data=data)
            artifact_id = sample_view_artifact_id(
                scene_id=record.scene_id, checksum=checksum
            )
            _, registered = await context.artifact_record_store.register(
                artifact_id=artifact_id,
                ref=ArtifactRef(
                    kind=ArtifactKind.SCENE_SAMPLE_VIEW_MANIFEST,
                    uri=uri,
                    media_type="application/json",
                    checksum=checksum,
                    size_bytes=published.size_bytes,
                    metadata={
                        "scene_id": record.scene_id,
                        "scene_manifest_checksum": record.manifest_checksum,
                        "sample_count": len(view.samples),
                        "dropped_anchor_count": len(view.dropped),
                        "policy_checksum": params.policy.checksum(),
                    },
                ),
                owner_type=ArtifactOwnerType.SCENE,
                owner_id=record.scene_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                scene_id=record.scene_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )
            if published.created or registered:
                created += 1
            else:
                reused += 1
            sample_count += len(view.samples)
            dropped += len(view.dropped)
            views.append(
                SampleViewRef(
                    scene_id=record.scene_id,
                    manifest_artifact_id=artifact_id,
                    manifest_checksum=checksum,
                )
            )
        await context.commit()

        if not views:
            raise ValueError(
                f"no Scene produced samples under the policy (skipped: {skipped[:10]})"
            )
        return BuildSceneSampleViewsJobResult(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            views=views,
            skipped=skipped,
            scene_count=len(views),
            sample_count=sample_count,
            dropped_anchor_count=dropped,
            created_count=created,
            reused_count=reused,
        )

    @staticmethod
    async def _scenes(
        context: WorkerContext, params: BuildSceneSampleViewsJobParams
    ) -> list[SceneRecord]:
        records = await list_dataset_version_scenes(
            context,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
        )
        if params.scene_ids:
            by_id = {r.scene_id: r for r in records}
            unknown = sorted(set(params.scene_ids) - set(by_id))
            if unknown:
                raise ValueError(
                    f"scenes not registered in {params.dataset_id}:"
                    f"{params.dataset_version}: {unknown[:10]}"
                )
            records = [by_id[s] for s in sorted(set(params.scene_ids))]
        if not records:
            raise ValueError(
                f"no registered scenes in {params.dataset_id}:{params.dataset_version}"
            )
        return sorted(records, key=lambda r: r.scene_id)
