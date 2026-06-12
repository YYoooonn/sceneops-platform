"""mine_scenarios — builds a ScenarioSetRecord from scene catalog signals.

Candidate profiles set default filter/sort behaviour.
User params in MineScenariosJobParams refine or override profile defaults.

Profiles:
  detection_ready  — GT required, annotation_count > 0, status validated/profiled
  no_gt_candidates — has_ground_truth=False (useful for pseudo-labeling)
  dense_gt         — GT required, sorted by annotation_count desc
  all              — no default filters
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sceneops_core.artifacts.schemas.enums import ArtifactKind
from sceneops_core.artifacts.schemas.owner import ArtifactOwnerType
from sceneops_core.artifacts.schemas.refs import ArtifactRef
from sceneops_core.common.ids import (
    generate_artifact_id,
    generate_scenario_set_id,
    default_mining_run_id,
)
from sceneops_core.common.time import utc_now
from sceneops_core.jobs.schemas import (
    JobType,
    MineScenariosJobParams,
    MineScenariosJobResult,
)
from sceneops_core.pipelines.schemas import PipelineTaskInputs
from sceneops_core.runs.schemas import RunStatus
from sceneops_core.scenarios.schemas.records import ScenarioSetRecord
from sceneops_core.scenarios.schemas.runs import ScenarioMiningRunRecord
from sceneops_core.scenes.schemas.enums import SceneStatus
from sceneops_worker.core.context import WorkerContext
from sceneops_worker.jobs.base import JobHandler, RunRecordHandler


# ── profile defaults ──────────────────────────────────────────────────────────

_PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "detection_ready": {
        "has_ground_truth": True,
        "min_annotation_count": 1,
        "require_validated_status": True,
    },
    "no_gt_candidates": {
        "has_ground_truth": False,
    },
    "dense_gt": {
        "has_ground_truth": True,
        "sort_by": "annotation_count",
        "order": "desc",
    },
    "all": {},
}

_VALIDATED_STATUSES = {SceneStatus.VALIDATED, SceneStatus.PROFILED}

# Sort key extractors
_SORT_KEYS = {
    "annotation_count": lambda c: c["annotation_count"],
    "sample_count": lambda c: c["sample_count"],
    "frame_count": lambda c: c["frame_count"],
    "scene_id": lambda c: c["scene_id"],
}


def _get_profile_defaults(profile: str) -> dict[str, Any]:
    return _PROFILE_DEFAULTS.get(profile, {})


def _passes_filters(
    scene_id: str,
    status: str,
    annotation_count: int,
    sample_count: int,
    frame_count: int,
    has_ground_truth: bool,
    channels: list[str],
    params: MineScenariosJobParams,
    profile: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Returns (passes, exclusion_reasons)."""
    exclusion_reasons: list[str] = []

    # has_ground_truth — user param wins over profile default
    req_gt = (
        params.has_ground_truth
        if params.has_ground_truth is not None
        else profile.get("has_ground_truth")
    )
    if req_gt is not None and has_ground_truth != req_gt:
        exclusion_reasons.append("gt_mismatch")

    # min_annotation_count — user param wins
    min_ann = (
        params.min_annotation_count
        if params.min_annotation_count is not None
        else profile.get("min_annotation_count")
    )
    if min_ann is not None and annotation_count < min_ann:
        exclusion_reasons.append("annotation_count_below_min")

    # max_annotation_count
    if (
        params.max_annotation_count is not None
        and annotation_count > params.max_annotation_count
    ):
        exclusion_reasons.append("annotation_count_above_max")

    # validation status gate (profile default for detection_ready)
    require_validated = profile.get("require_validated_status", False)
    if require_validated and status not in _VALIDATED_STATUSES:
        exclusion_reasons.append("not_validated")

    # required channels (user param only)
    if params.required_channels:
        scene_channel_set = set(channels)
        missing = [ch for ch in params.required_channels if ch not in scene_channel_set]
        if missing:
            exclusion_reasons.append(f"missing_channels:{','.join(missing)}")

    # selectable_for_detection: derived as has_gt AND annotation_count > 0
    if params.selectable_for_detection is not None:
        derived_selectable = has_ground_truth and annotation_count > 0
        if derived_selectable != params.selectable_for_detection:
            exclusion_reasons.append("selectable_for_detection_mismatch")

    return (len(exclusion_reasons) == 0), exclusion_reasons


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
        return {
            "dataset_id": inputs.dataset.dataset_id if inputs.dataset else None,
            "dataset_version": inputs.dataset.dataset_version
            if inputs.dataset
            else None,
            **inputs.params,
        }

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
        dataset_id = params.dataset_id
        dataset_version = params.dataset_version
        profile = _get_profile_defaults(params.candidate_profile)

        # ── load all scenes for this dataset version ──────────────────────────
        all_scenes = []
        offset = 0
        page_size = 500
        while True:
            page = await context.scene_store.list(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                limit=page_size,
                offset=offset,
            )
            all_scenes.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

        input_scene_count = len(all_scenes)

        # ── filter ────────────────────────────────────────────────────────────
        selected: list[dict[str, Any]] = []
        rejected_count = 0

        for scene in all_scenes:
            passes, exclusion_reasons = _passes_filters(
                scene_id=scene.scene_id,
                status=str(scene.status),
                annotation_count=scene.annotation_count,
                sample_count=scene.sample_count,
                frame_count=scene.frame_count,
                has_ground_truth=scene.has_ground_truth,
                channels=scene.channels,
                params=params,
                profile=profile,
            )

            candidate = {
                "candidate_id": scene.scene_id,
                "scene_id": scene.scene_id,
                "dataset_id": dataset_id,
                "dataset_version": dataset_version,
                "status": str(scene.status),
                "sample_count": scene.sample_count,
                "frame_count": scene.frame_count,
                "annotation_count": scene.annotation_count,
                "has_ground_truth": scene.has_ground_truth,
                "ground_truth_source": scene.ground_truth_source,
                "channels": scene.channels,
                "selectable_for_detection": scene.has_ground_truth
                and scene.annotation_count > 0,
                "validation_status": (
                    "ready"
                    if str(scene.status) in ("validated", "profiled")
                    else "blocked"
                    if str(scene.status) == "failed"
                    else "unknown"
                ),
                "exclusion_reasons": exclusion_reasons,
            }

            if passes:
                selected.append(candidate)
            else:
                rejected_count += 1

        # ── sort ──────────────────────────────────────────────────────────────
        sort_by = (
            params.sort_by
            if params.sort_by
            else profile.get("sort_by", "annotation_count")
        )
        order = params.order if params.order else profile.get("order", "desc")
        sort_key = _SORT_KEYS.get(sort_by, _SORT_KEYS["annotation_count"])
        selected.sort(key=sort_key, reverse=(order == "desc"))

        # ── limit ─────────────────────────────────────────────────────────────
        candidates = selected[: params.max_candidates]
        selected_count = len(candidates)
        selected_scene_ids = [c["scene_id"] for c in candidates]

        # ── create scenario set ───────────────────────────────────────────────
        scenario_set_id = params.output_scenario_set_id or generate_scenario_set_id()

        # ── write candidates artifact ─────────────────────────────────────────
        scenario_set_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri,
            "scenario_mining",
            run_id,
            "candidates.json",
        )

        candidates_payload: dict[str, Any] = {
            "scenario_set_id": scenario_set_id,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "candidate_profile": params.candidate_profile,
            "created_at": utc_now().isoformat(),
            "pipeline_run_id": job.pipeline_run_id,
            "job_id": job.job_id,
            "input_scene_count": input_scene_count,
            "candidate_count": selected_count,
            "selected_count": selected_count,
            "rejected_count": rejected_count,
            "filters": {
                "has_ground_truth": params.has_ground_truth,
                "selectable_for_detection": params.selectable_for_detection,
                "min_annotation_count": params.min_annotation_count,
                "max_annotation_count": params.max_annotation_count,
                "required_channels": params.required_channels,
                "sort_by": sort_by,
                "order": order,
                "max_candidates": params.max_candidates,
            },
            "candidates": candidates,
        }
        await context.artifact_store.write_json(scenario_set_uri, candidates_payload)

        # ── write mining report ───────────────────────────────────────────────
        mining_report_uri = context.artifact_store.join_uri(
            context.settings.run_root_uri,
            "scenario_mining",
            run_id,
            "report.json",
        )
        summary_payload: dict[str, Any] = {
            "run_id": run_id,
            "job_id": job.job_id,
            "candidate_profile": params.candidate_profile,
            "input_scene_count": input_scene_count,
            "candidate_count": selected_count,
            "selected_count": selected_count,
            "rejected_count": rejected_count,
            "created_at": utc_now().isoformat(),
        }
        await context.artifact_store.write_json(mining_report_uri, summary_payload)

        # ── persist ScenarioSetRecord ─────────────────────────────────────────
        scenario_set_record = ScenarioSetRecord(
            scenario_set_id=scenario_set_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            name=f"{params.candidate_profile} — {dataset_id}/{dataset_version}",
            scenario_set_uri=scenario_set_uri,
            scenario_count=selected_count,
        )
        await context.scenario_store.upsert(scenario_set_record)

        # ── register artifacts ────────────────────────────────────────────────
        for uri, kind in [
            (scenario_set_uri, ArtifactKind.SCENARIO_SET_MANIFEST),
            (mining_report_uri, ArtifactKind.SCENARIO_MINING_REPORT),
        ]:
            await context.artifact_record_store.create(
                artifact_id=generate_artifact_id(),
                ref=ArtifactRef(kind=kind, uri=uri, media_type="application/json"),
                owner_type=ArtifactOwnerType.SCENARIO_MINING_RUN,
                owner_id=run_id,
                scenario_set_id=scenario_set_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                run_id=run_id,
                job_id=job.job_id,
                pipeline_run_id=job.pipeline_run_id,
            )

        # ── update + persist run record ───────────────────────────────────────
        summary = {
            "candidate_profile": params.candidate_profile,
            "predicate": {
                k: v for k, v in candidates_payload["filters"].items() if v is not None
            },
            "counts": {
                "input_scene_count": input_scene_count,
                "candidate_count": selected_count,
                "selected_count": selected_count,
                "rejected_count": rejected_count,
            },
            "selection": {
                "selected_scene_ids": selected_scene_ids,
            },
        }

        succeeded_record = initial_record.model_copy(
            update={
                "status": RunStatus.SUCCEEDED,
                "scenario_set_id": scenario_set_id,
                "scenario_set_uri": scenario_set_uri,
                "mining_report_uri": mining_report_uri,
                "candidate_count": selected_count,
                "selected_count": selected_count,
                "rejected_count": rejected_count,
                "summary": summary,
                "finished_at": utc_now(),
            }
        )

        return succeeded_record, MineScenariosJobResult(
            scenario_set_id=scenario_set_id,
            scenario_set_uri=scenario_set_uri,
            report_uri=mining_report_uri,
            mining_run_id=run_id,
            candidate_count=selected_count,
            selected_count=selected_count,
            rejected_count=rejected_count,
            selected_scene_ids=selected_scene_ids,
            summary=summary,
        )

    async def _upsert(
        self, context: WorkerContext, record: ScenarioMiningRunRecord
    ) -> ScenarioMiningRunRecord:
        result = await context.scenario_store.upsert_run(record)
        assert isinstance(result, ScenarioMiningRunRecord)
        return result
