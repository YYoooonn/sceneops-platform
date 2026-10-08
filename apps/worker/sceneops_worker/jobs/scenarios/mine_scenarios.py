"""mine_scenarios: curate a ScenarioSet revision from pinned sample views
(ADR-007 §33.4).

The input is an explicit list of SceneSampleView revisions, never a scan of
a DatasetVersion. Label criteria count the labels of one pinned label set
revision that every input view must pin. The output is an immutable,
checksum-pinned ScenarioSetManifest; re-running with the same inputs and
parameters produces the same revision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.canonical_json import canonical_json_bytes
from sceneops_core.common.derived_ids import scenario_set_artifact_id
from sceneops_core.common.ids import default_mining_run_id, default_scenario_set_id
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobType,
    MineScenariosJobParams,
    MineScenariosJobResult,
)
from sceneops_core.labels import LabelSetRef
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.sample_views import SampleViewRef, SceneSampleViewManifest
from sceneops_core.scenarios import (
    ScenarioCuration,
    ScenarioMember,
    ScenarioSetManifest,
    ScenarioSortKey,
    SortOrder,
)
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_core.scenarios.schemas.runs import ScenarioMiningRunRecord
from sceneops_worker.core.context import WorkerContext
from sceneops_derived import DerivedManifestConflictError
from sceneops_worker.derived.publication import publish_registered
from sceneops_worker.derived.resolution import resolve_sample_view
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler
from sceneops_worker.scenes.readiness import pinned_revision_readiness


class LabelSetPinMismatchError(ValueError):
    """Input views pin no label set, or different revisions of it."""


class DatasetScopeMismatchError(ValueError):
    """A pinned view belongs to a different DatasetVersion than the run."""


@dataclass(frozen=True)
class _Candidate:
    member: ScenarioMember | None
    exclusion_reasons: list[str]


def _label_set_pin(
    views: list[SceneSampleViewManifest], label_set_id: str
) -> LabelSetRef:
    """The one revision of ``label_set_id`` every input view pins."""
    revisions: dict[str, LabelSetRef] = {}
    for view in views:
        pinned = {ref.label_set_id: ref for ref in view.label_sets}
        ref = pinned.get(label_set_id)
        if ref is None:
            raise LabelSetPinMismatchError(
                f"sample view of scene {view.scene.scene_id} does not pin label "
                f"set {label_set_id!r}"
            )
        revisions[ref.manifest_checksum] = ref
    if len(revisions) != 1:
        raise LabelSetPinMismatchError(
            f"input views pin {len(revisions)} different revisions of label set "
            f"{label_set_id!r}: {sorted(revisions)}"
        )
    return next(iter(revisions.values()))


def _evaluate_view(
    *,
    view: SceneSampleViewManifest,
    view_ref: SampleViewRef,
    label_set: LabelSetRef | None,
    readiness: str,
    curation: ScenarioCuration,
) -> _Candidate:
    """The member this view would contribute, with the reasons it is excluded
    (empty when it passes)."""
    samples = view.samples
    label_counts: dict[str, int] = {}
    covered: set[str] = set()
    if label_set is not None:
        for sample in samples:
            entry = next(
                e for e in sample.labels if e.label_set_id == label_set.label_set_id
            )
            if entry.covered:
                covered.add(sample.sample_id)
            label_counts[sample.sample_id] = len(entry.label_ids)

    selected = [
        s
        for s in samples
        if not (curation.require_labels and s.sample_id not in covered)
    ]
    label_count = sum(label_counts.get(s.sample_id, 0) for s in selected)
    channels = sorted(
        {
            c
            for s in selected
            for c in (s.anchor.channel, *(m.channel for m in s.members))
        }
    )

    reasons: list[str] = []
    if not selected:
        reasons.append("no_selectable_samples")
    if curation.require_labels and label_count < 1:
        reasons.append("no_labels")
    if curation.min_label_count is not None and label_count < curation.min_label_count:
        reasons.append("label_count_below_min")
    if curation.max_label_count is not None and label_count > curation.max_label_count:
        reasons.append("label_count_above_max")
    if (
        curation.min_sample_count is not None
        and len(selected) < curation.min_sample_count
    ):
        reasons.append("sample_count_below_min")
    missing = [c for c in curation.required_channels if c not in channels]
    if missing:
        reasons.append(f"missing_channels:{','.join(missing)}")
    if curation.readiness is not None and readiness not in curation.readiness:
        reasons.append("readiness_not_allowed")

    if not selected:
        return _Candidate(member=None, exclusion_reasons=reasons)
    member = ScenarioMember(
        scene_id=view.scene.scene_id,
        sample_view=view_ref,
        sample_ids=sorted(s.sample_id for s in selected),
        sample_count=len(samples),
        label_count=label_count,
        channels=channels,
        readiness=readiness,
    )
    return _Candidate(member=member, exclusion_reasons=reasons)


_SORT_KEYS = {
    ScenarioSortKey.LABEL_COUNT: lambda m: m.label_count,
    ScenarioSortKey.SAMPLE_COUNT: lambda m: len(m.sample_ids),
    ScenarioSortKey.SCENE_ID: lambda m: m.scene_id,
}


class MineScenariosJobHandler(
    RunRecordHandler[
        MineScenariosJobParams, MineScenariosJobResult, ScenarioMiningRunRecord
    ],
    JobHandler[MineScenariosJobParams, MineScenariosJobResult],
):
    @property
    def job_type(self) -> JobType:
        return JobType.MINE_SCENARIOS

    @property
    def params_model(self) -> type[MineScenariosJobParams]:
        return MineScenariosJobParams

    def build_job_params(self, inputs: PipelineTaskInputs) -> dict:
        params: dict = {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }
        # In a pipeline the views are the pinned revisions the upstream
        # build_scene_sample_views stage published.
        if "sample_views" not in params and inputs.refs.get("views"):
            params["sample_views"] = inputs.refs["views"]
        return params

    def build_initial_record(
        self,
        *,
        job: Any,
        params: MineScenariosJobParams,
        started_at: datetime,
    ) -> ScenarioMiningRunRecord:
        return ScenarioMiningRunRecord(
            run_id=default_mining_run_id(job.job_id),
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            status=RunStatus.RUNNING,
            pipeline_run_id=job.pipeline_run_id,
            pipeline_task_run_id=job.pipeline_task_run_id,
            job_id=job.job_id,
            started_at=started_at,
        )

    async def execute(
        self,
        *,
        job: Any,
        params: MineScenariosJobParams,
        context: WorkerContext,
        initial_record: ScenarioMiningRunRecord,
        started_at: datetime,
    ) -> tuple[ScenarioMiningRunRecord, MineScenariosJobResult]:
        run_id = initial_record.run_id

        resolved = []
        for ref in sorted(set(params.sample_views), key=lambda r: r.scene_id):
            item = await resolve_sample_view(context, ref)
            if (item.dataset_id, item.dataset_version) != (
                params.dataset_id,
                params.dataset_version,
            ):
                raise DatasetScopeMismatchError(
                    f"sample view {ref.manifest_artifact_id} belongs to "
                    f"{item.dataset_id}:{item.dataset_version}, not "
                    f"{params.dataset_id}:{params.dataset_version}"
                )
            resolved.append(item)
        views = [item.view for item in resolved]
        scene_ids = [v.scene.scene_id for v in views]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("more than one sample view was given for a scene")

        label_set = (
            _label_set_pin(views, params.label_set_id)
            if params.label_set_id is not None
            else None
        )
        curation = ScenarioCuration(
            label_set=label_set,
            require_labels=params.require_labels,
            min_label_count=params.min_label_count,
            max_label_count=params.max_label_count,
            min_sample_count=params.min_sample_count,
            required_channels=sorted(set(params.required_channels)),
            readiness=sorted(set(params.readiness))
            if params.readiness is not None
            else None,
            sort_by=params.sort_by,
            order=params.order,
            max_candidates=params.max_candidates,
        )

        readiness = await pinned_revision_readiness(context, [v.scene for v in views])

        passing: list[ScenarioMember] = []
        rejected: list[dict[str, Any]] = []
        for item in resolved:
            candidate = _evaluate_view(
                view=item.view,
                view_ref=item.ref,
                label_set=label_set,
                readiness=readiness[item.view.scene.scene_id].value,
                curation=curation,
            )
            if candidate.exclusion_reasons:
                rejected.append(
                    {
                        "scene_id": item.view.scene.scene_id,
                        "reasons": candidate.exclusion_reasons,
                    }
                )
            else:
                assert candidate.member is not None
                passing.append(candidate.member)

        passing.sort(key=lambda m: m.scene_id)
        passing.sort(
            key=_SORT_KEYS[curation.sort_by], reverse=curation.order == SortOrder.DESC
        )
        members = passing[: curation.max_candidates]
        for member in passing[curation.max_candidates :]:
            rejected.append(
                {"scene_id": member.scene_id, "reasons": ["max_candidates"]}
            )

        scenario_set_id = params.output_scenario_set_id or default_scenario_set_id(
            job.job_id
        )
        manifest = ScenarioSetManifest(
            scenario_set_id=scenario_set_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            curation=curation,
            input_scene_count=len(views),
            rejected_scene_count=len(views) - len(members),
            members=members,
        )
        checksum = manifest.checksum()
        existing = await context.scenario_store.get(scenario_set_id)
        if (
            existing is not None
            and existing.manifest_checksum is not None
            and existing.manifest_checksum != checksum
        ):
            raise DerivedManifestConflictError(
                f"ScenarioSet {scenario_set_id!r} already exists as revision "
                f"{existing.manifest_checksum}; scenario sets are immutable, so "
                "choose another output_scenario_set_id"
            )

        uri = context.derived_store.scenario_set_uri(
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            scenario_set_id=scenario_set_id,
            checksum=checksum,
        )
        published = await context.derived_store.publish(
            uri=uri, data=manifest.to_canonical_bytes()
        )
        manifest_artifact_id = scenario_set_artifact_id(
            scenario_set_id=scenario_set_id, checksum=checksum
        )
        await context.artifact_record_store.register(
            artifact_id=manifest_artifact_id,
            ref=ArtifactRef(
                kind=ArtifactKind.SCENARIO_SET_MANIFEST,
                uri=uri,
                media_type="application/json",
                checksum=checksum,
                size_bytes=published.size_bytes,
            ),
            owner_type=ArtifactOwnerType.SCENARIO_SET,
            owner_id=scenario_set_id,
            scenario_set_id=scenario_set_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )

        selected_scene_ids = [m.scene_id for m in members]
        report = await publish_registered(
            context,
            kind=ArtifactKind.SCENARIO_MINING_REPORT,
            prefix="miningreport",
            logical_id=run_id,
            directory=context.artifact_store.join_uri(
                context.settings.run_root_uri, "scenario_mining", run_id
            ),
            stem="report",
            data=canonical_json_bytes(
                {
                    "run_id": run_id,
                    "job_id": job.job_id,
                    "scenario_set_id": scenario_set_id,
                    "scenario_set_checksum": checksum,
                    "input_scene_count": len(views),
                    "selected_count": len(members),
                    "rejected_count": len(views) - len(members),
                    "rejected": rejected,
                }
            ),
            media_type="application/json",
            owner_type=ArtifactOwnerType.SCENARIO_MINING_RUN,
            owner_id=run_id,
            scenario_set_id=scenario_set_id,
            dataset_id=params.dataset_id,
            dataset_version=params.dataset_version,
            run_id=run_id,
            job_id=job.job_id,
            pipeline_run_id=job.pipeline_run_id,
        )
        report_uri = report.uri

        await context.scenario_store.upsert(
            ScenarioSetRecord(
                scenario_set_id=scenario_set_id,
                dataset_id=params.dataset_id,
                dataset_version=params.dataset_version,
                name=f"{params.dataset_id}/{params.dataset_version} scenario set",
                scenario_set_uri=uri,
                manifest_artifact_id=manifest_artifact_id,
                manifest_checksum=checksum,
                scenario_count=len(members),
            )
        )

        summary = {
            "predicate": curation.model_dump(mode="json", exclude_none=True),
            "counts": {
                "input_scene_count": len(views),
                "selected_count": len(members),
                "rejected_count": len(views) - len(members),
            },
            "selection": {"selected_scene_ids": selected_scene_ids},
            "scenario_set": {
                "scenario_set_id": scenario_set_id,
                "manifest_artifact_id": manifest_artifact_id,
                "manifest_checksum": checksum,
            },
        }
        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "scenario_set_id": scenario_set_id,
                "scenario_set_uri": uri,
                "mining_report_uri": report_uri,
                "candidate_count": len(members),
                "selected_count": len(members),
                "rejected_count": len(views) - len(members),
                "summary": summary,
                "finished_at": utc_now(),
            }
        )
        return succeeded_record, MineScenariosJobResult(
            scenario_set_id=scenario_set_id,
            scenario_set_uri=uri,
            scenario_set_checksum=checksum,
            scenario_set_manifest_artifact_id=manifest_artifact_id,
            report_uri=report_uri,
            mining_run_id=run_id,
            candidate_count=len(members),
            selected_count=len(members),
            rejected_count=len(views) - len(members),
            selected_scene_ids=selected_scene_ids,
            summary=summary,
        )

    async def _upsert(
        self, context: WorkerContext, record: ScenarioMiningRunRecord
    ) -> ScenarioMiningRunRecord:
        result = await context.scenario_store.upsert_run(record)
        assert isinstance(result, ScenarioMiningRunRecord)
        return result
