from __future__ import annotations

from sceneops_core.jobs.schemas.enums import JobType
from sceneops_core.jobs.schemas.steps import (
    JobStep,
    JobStepDefinition,
    JobStepStatus,
)


def step(
    step_id: str,
    name: str,
    *,
    description: str | None = None,
    optional: bool = False,
) -> JobStepDefinition:
    return JobStepDefinition(
        job_step_id=step_id,
        job_step_name=name,
        description=description,
        optional=optional,
    )


JOB_STEP_DEFINITIONS_BY_TYPE: dict[JobType, list[JobStepDefinition]] = {
    JobType.INGEST_SCENES: [
        step("load_source_dataset", "Load source dataset"),
        step("convert_scenes", "Convert scenes"),
        step("save_scene_manifests", "Save scene manifests"),
    ],
    JobType.BUILD_SCENES: [
        step("load_raw_log", "Load raw log"),
        step("segment_scenes", "Segment scenes"),
        step("compose_scene_manifests", "Compose scene manifests"),
        step("build_assets", "Build assets", optional=True),
        step("build_world_state", "Build world state", optional=True),
        step("save_scene_manifests", "Save scene manifests"),
    ],
    JobType.BUILD_DATASET_MANIFEST: [
        step("load_scene_manifests", "Load scene manifests"),
        step("aggregate_dataset_index", "Aggregate dataset index"),
        step("save_dataset_manifest", "Save dataset manifest"),
    ],
    JobType.BUILD_SCENE_INDEX: [
        step("load_scene_manifests", "Load scene manifests"),
        step("build_scene_index", "Build scene index"),
        step("save_scene_index", "Save scene index"),
    ],
    JobType.VALIDATE_SCENE: [
        step("load_scene_manifest", "Load scene manifest"),
        step("validate_scene_structure", "Validate scene structure"),
        step("validate_required_channels", "Validate required channels", optional=True),
        step("validate_assets", "Validate assets", optional=True),
        step("validate_world_state", "Validate world state", optional=True),
        step("save_validation_report", "Save validation report"),
    ],
    JobType.PROFILE_SCENE: [
        step("load_scene_manifest", "Load scene manifest"),
        step("profile_samples", "Profile samples"),
        step("profile_assets", "Profile assets", optional=True),
        step("profile_world_state", "Profile world state", optional=True),
        step("save_profile_report", "Save profile report"),
    ],
    JobType.REGISTER_SCENE: [
        step("load_scene_manifest", "Load scene manifest"),
        step("validate_registration", "Validate registration"),
        step("upsert_scene_record", "Upsert scene record"),
    ],
    JobType.COMPARE_SCENES: [
        step("load_source_scene", "Load source scene"),
        step("load_target_scene", "Load target scene"),
        step("compare_geometry", "Compare geometry", optional=True),
        step("compare_annotations", "Compare annotations", optional=True),
        step("compare_trajectories", "Compare trajectories", optional=True),
        step("save_comparison_report", "Save comparison report"),
    ],
    JobType.AUTO_LABEL_SCENE: [
        step("load_scene_manifest", "Load scene manifest"),
        step("run_labeler", "Run labeler"),
        step("merge_labels", "Merge labels"),
        step("save_labeled_scene", "Save labeled scene"),
    ],
    JobType.EXPORT_SCENE_PACKAGE: [
        step("load_scene_manifest", "Load scene manifest"),
        step("collect_scene_artifacts", "Collect scene artifacts"),
        step("write_scene_package", "Write scene package"),
    ],
    JobType.MINE_SCENARIOS: [
        step("load_dataset_manifest", "Load dataset manifest"),
        step("load_scene_manifests", "Load scene manifests"),
        step("apply_predicates", "Apply predicates"),
        step("select_scenarios", "Select scenarios"),
        step("save_scenario_set", "Save scenario set"),
    ],
    JobType.SCORE_SCENARIO_READINESS: [
        step("load_scenario_set", "Load scenario set"),
        step("score_scenarios", "Score scenarios"),
        step("save_readiness_report", "Save readiness report"),
    ],
    JobType.AUTO_LABEL_DATASET: [
        step("load_dataset_manifest", "Load dataset manifest"),
        step("run_scene_labeling", "Run scene labeling"),
        step("build_labeled_dataset_manifest", "Build labeled dataset manifest"),
    ],
    JobType.CHECK_DISTRIBUTION: [
        step("load_dataset_manifest", "Load dataset manifest"),
        step("compute_distribution", "Compute distribution"),
        step("compare_distribution", "Compare distribution", optional=True),
        step("save_distribution_report", "Save distribution report"),
    ],
    JobType.EXPORT_DATASET: [
        step("load_dataset_manifest", "Load dataset manifest"),
        step("collect_dataset_artifacts", "Collect dataset artifacts"),
        step("write_dataset_export", "Write dataset export"),
    ],
    JobType.EXPORT_ANALYTICS_SNAPSHOT: [
        step("load_scene_records", "Load scene records"),
        step("load_scene_manifests", "Load scene manifests"),
        step("build_analytics_tables", "Build analytics tables"),
        step("write_parquet_tables", "Write parquet tables"),
    ],
    JobType.PREDICT_DETECTION: [
        step("load_dataset_manifest", "Load dataset manifest"),
        step("load_model", "Load model"),
        step("run_inference", "Run inference"),
        step("save_predictions", "Save predictions"),
    ],
    JobType.EVALUATE_DETECTION: [
        step("load_predictions", "Load predictions"),
        step("load_ground_truth", "Load ground truth"),
        step("match_detections", "Match detections"),
        step("compute_metrics", "Compute metrics"),
        step("save_metrics", "Save metrics"),
    ],
}


def get_job_step_definitions(job_type: JobType) -> list[JobStepDefinition]:
    try:
        return JOB_STEP_DEFINITIONS_BY_TYPE[job_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported job type: {job_type}") from exc


def create_initial_job_steps(job_type: JobType) -> list[JobStep]:
    return [
        JobStep(
            job_step_id=definition.job_step_id,
            job_step_name=definition.job_step_name,
            status=JobStepStatus.PENDING,
            metadata={
                **definition.metadata,
                "description": definition.description,
                "optional": definition.optional,
            },
        )
        for definition in get_job_step_definitions(job_type)
    ]
