from __future__ import annotations

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.registry import PipelineDefinitionRegistry
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineTaskDefinition,
    PipelineTaskOutputKind,
    PipelineTaskOutputSpec,
    PipelineTaskQualityRule,
    PipelineTaskQualityRuleType,
    PipelineType,
)

# ── Shared output / quality-rule declarations ──────────────────────────────────
# Reused across pipeline definitions that share the same task type.

_REF = PipelineTaskOutputKind.REF
_SUMMARY = PipelineTaskOutputKind.SUMMARY
_METRIC = PipelineTaskOutputKind.METRIC
_ARTIFACT = PipelineTaskOutputKind.ARTIFACT

_INGEST_SCENES_OUTPUTS = [
    PipelineTaskOutputSpec(
        name="scene_manifest_uris", kind=_REF, source="scene_manifest_uris"
    ),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(name="frame_count", kind=_SUMMARY, source="frame_count"),
]

_BUILD_SCENES_OUTPUTS = [
    # scene_manifest_uris consumed by register_scene → REF.
    PipelineTaskOutputSpec(
        name="scene_manifest_uris", kind=_REF, source="scene_manifest_uris"
    ),
    # Diagnostic/archival URIs not consumed by downstream tasks → ARTIFACT.
    PipelineTaskOutputSpec(
        name="scene_segment_index_uri", kind=_ARTIFACT, source="scene_segment_index_uri"
    ),
    PipelineTaskOutputSpec(
        name="raw_log_manifest_uri", kind=_ARTIFACT, source="raw_log_manifest_uri"
    ),
    PipelineTaskOutputSpec(
        name="raw_log_frame_index_uri", kind=_ARTIFACT, source="raw_log_frame_index_uri"
    ),
    PipelineTaskOutputSpec(name="records_uri", kind=_ARTIFACT, source="records_uri"),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(name="frame_count", kind=_SUMMARY, source="frame_count"),
    PipelineTaskOutputSpec(name="source_type", kind=_SUMMARY, source="source_type"),
    PipelineTaskOutputSpec(name="source_format", kind=_SUMMARY, source="source_format"),
    PipelineTaskOutputSpec(
        name="observation_count", kind=_SUMMARY, source="observation_count"
    ),
    PipelineTaskOutputSpec(
        name="segmentation_strategy", kind=_SUMMARY, source="segmentation_strategy"
    ),
    PipelineTaskOutputSpec(
        name="sampling_strategy", kind=_SUMMARY, source="sampling_strategy"
    ),
    PipelineTaskOutputSpec(
        name="sample_count_before_filtering",
        kind=_SUMMARY,
        source="sample_count_before_filtering",
    ),
    PipelineTaskOutputSpec(
        name="sample_count_after_filtering",
        kind=_SUMMARY,
        source="sample_count_after_filtering",
    ),
    PipelineTaskOutputSpec(
        name="dropped_sample_count", kind=_SUMMARY, source="dropped_sample_count"
    ),
    PipelineTaskOutputSpec(
        name="warned_sample_count", kind=_SUMMARY, source="warned_sample_count"
    ),
    PipelineTaskOutputSpec(
        name="samples_with_missing_channels_count",
        kind=_SUMMARY,
        source="samples_with_missing_channels_count",
    ),
    PipelineTaskOutputSpec(
        name="missing_channel_counts_by_channel",
        kind=_SUMMARY,
        source="missing_channel_counts_by_channel",
    ),
]

_REGISTER_SCENE_OUTPUTS = [
    PipelineTaskOutputSpec(
        name="scene_manifest_uris", kind=_REF, source="scene_manifest_uris"
    ),
    PipelineTaskOutputSpec(
        name="registered_scene_count", kind=_SUMMARY, source="registered_scene_count"
    ),
]

_VALIDATE_SCENE_OUTPUTS = [
    # validation_run_id kept as REF for cross-referencing from quality cache.
    PipelineTaskOutputSpec(
        name="validation_run_id", kind=_REF, source="validation_run_id"
    ),
    # Report URI not consumed by downstream tasks → ARTIFACT.
    PipelineTaskOutputSpec(
        name="validation_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="validation_report_uri",
    ),
    PipelineTaskOutputSpec(
        name="validation_status",
        kind=_SUMMARY,
        source="status",
        target="validation_status",
    ),
    PipelineTaskOutputSpec(
        name="should_block_pipeline", kind=_SUMMARY, source="should_block_pipeline"
    ),
    PipelineTaskOutputSpec(name="issue_count", kind=_SUMMARY, source="issue_count"),
    PipelineTaskOutputSpec(
        name="checked_scene_count", kind=_SUMMARY, source="checked_scene_count"
    ),
]

_VALIDATE_SCENE_QUALITY_RULES = [
    PipelineTaskQualityRule(
        rule_type=PipelineTaskQualityRuleType.BLOCK_IF_TRUE,
        source="summary.should_block_pipeline",
        message="Scene validation blocked pipeline",
        code="validate_scene_blocked",
    ),
]

_PROFILE_SCENE_OUTPUTS = [
    # profile_run_id kept as REF for cross-referencing.
    PipelineTaskOutputSpec(name="profile_run_id", kind=_REF, source="profile_run_id"),
    # Report URI not consumed by downstream tasks → ARTIFACT.
    PipelineTaskOutputSpec(
        name="profile_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="profile_report_uri",
    ),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(name="frame_count", kind=_SUMMARY, source="frame_count"),
    PipelineTaskOutputSpec(
        name="annotation_count", kind=_SUMMARY, source="annotation_count"
    ),
    PipelineTaskOutputSpec(
        name="observed_channels", kind=_SUMMARY, source="observed_channels"
    ),
]

_BUILD_SCENE_INDEX_OUTPUTS = [
    # scene_manifest_uris consumed by build_dataset_manifest → REF.
    PipelineTaskOutputSpec(
        name="scene_manifest_uris", kind=_REF, source="scene_manifest_uris"
    ),
    # scene_index_uri not consumed downstream → ARTIFACT.
    PipelineTaskOutputSpec(
        name="scene_index_uri", kind=_ARTIFACT, source="scene_index_uri"
    ),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(name="frame_count", kind=_SUMMARY, source="frame_count"),
]

_BUILD_DATASET_MANIFEST_OUTPUTS = [
    PipelineTaskOutputSpec(
        name="dataset_manifest_uri", kind=_REF, source="dataset_manifest_uri"
    ),
    PipelineTaskOutputSpec(name="scene_count", kind=_SUMMARY, source="scene_count"),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(name="frame_count", kind=_SUMMARY, source="frame_count"),
]

_PREDICT_DETECTION_OUTPUTS = [
    # inference_run_id consumed by evaluate_detection → REF.
    PipelineTaskOutputSpec(
        name="inference_run_id", kind=_REF, source="inference_run_id"
    ),
    # Prediction file URIs not consumed downstream → ARTIFACT.
    PipelineTaskOutputSpec(
        name="prediction_manifest_uri", kind=_ARTIFACT, source="prediction_manifest_uri"
    ),
    PipelineTaskOutputSpec(
        name="predictions_root_uri", kind=_ARTIFACT, source="predictions_root_uri"
    ),
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(
        name="prediction_count", kind=_SUMMARY, source="prediction_count"
    ),
]

_EVALUATE_DETECTION_OUTPUTS = [
    # Run IDs kept as REFs for cross-referencing.
    PipelineTaskOutputSpec(
        name="evaluation_run_id", kind=_REF, source="evaluation_run_id"
    ),
    PipelineTaskOutputSpec(
        name="inference_run_id", kind=_REF, source="inference_run_id"
    ),
    # File URIs not consumed downstream → ARTIFACT.
    PipelineTaskOutputSpec(
        name="evaluation_manifest_uri", kind=_ARTIFACT, source="evaluation_manifest_uri"
    ),
    PipelineTaskOutputSpec(name="metrics_uri", kind=_ARTIFACT, source="metrics_uri"),
    # Counts / summary fields.
    PipelineTaskOutputSpec(name="sample_count", kind=_SUMMARY, source="sample_count"),
    PipelineTaskOutputSpec(
        name="prediction_count", kind=_SUMMARY, source="prediction_count"
    ),
    PipelineTaskOutputSpec(
        name="evaluable_prediction_count",
        kind=_SUMMARY,
        source="evaluable_prediction_count",
    ),
    PipelineTaskOutputSpec(
        name="lifting_failed_prediction_count",
        kind=_SUMMARY,
        source="lifting_failed_prediction_count",
    ),
    PipelineTaskOutputSpec(
        name="ground_truth_count", kind=_SUMMARY, source="ground_truth_count"
    ),
    PipelineTaskOutputSpec(
        name="evaluation_unit", kind=_SUMMARY, source="evaluation_unit"
    ),
    PipelineTaskOutputSpec(
        name="primary_metric_name", kind=_METRIC, source="primary_metric_name"
    ),
    PipelineTaskOutputSpec(
        name="primary_metric_value", kind=_METRIC, source="primary_metric_value"
    ),
]

# ── Pipeline definitions ───────────────────────────────────────────────────────

DATASET_SCENE_INGESTION_PIPELINE = PipelineDefinition(
    type=PipelineType.DATASET_SCENE_INGESTION,
    name="Dataset Scene Ingestion",
    description=(
        "Import existing scene-aware datasets such as nuScenes, Waymo, or KITTI "
        "into SceneOps scene manifests, register them, then build a dataset manifest."
    ),
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="ingest_scenes",
            name="Ingest scenes",
            order=0,
            job_type=JobType.INGEST_SCENES,
            default_params={
                "source_format": "nuscenes",
                "mode": "upsert",
            },
            outputs=_INGEST_SCENES_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="register_scene",
            name="Register scenes",
            order=1,
            job_type=JobType.REGISTER_SCENE,
            depends_on_pipeline_task_ids=["ingest_scenes"],
            default_params={
                "replace_existing": True,
            },
            outputs=_REGISTER_SCENE_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_scene",
            name="Validate scene",
            order=2,
            job_type=JobType.VALIDATE_SCENE,
            depends_on_pipeline_task_ids=["register_scene"],
            outputs=_VALIDATE_SCENE_OUTPUTS,
            quality_rules=_VALIDATE_SCENE_QUALITY_RULES,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_scene",
            name="Profile scene",
            order=3,
            job_type=JobType.PROFILE_SCENE,
            depends_on_pipeline_task_ids=["register_scene"],
            default_params={
                "profile_samples": True,
                "profile_assets": True,
            },
            optional=True,
            outputs=_PROFILE_SCENE_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="build_scene_index",
            name="Build scene index",
            order=4,
            job_type=JobType.BUILD_SCENE_INDEX,
            depends_on_pipeline_task_ids=["register_scene"],
            outputs=_BUILD_SCENE_INDEX_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="build_dataset_manifest",
            name="Build dataset manifest",
            order=5,
            job_type=JobType.BUILD_DATASET_MANIFEST,
            depends_on_pipeline_task_ids=["build_scene_index"],
            outputs=_BUILD_DATASET_MANIFEST_OUTPUTS,
        ),
    ],
)


RAW_LOG_SCENE_BUILDING_PIPELINE = PipelineDefinition(
    type=PipelineType.RAW_LOG_SCENE_BUILDING,
    name="Raw Log Scene Building",
    description=(
        "Build SceneOps scene manifests from raw observation streams, register them, "
        "then build a scene index and dataset manifest. "
        "Local E2E uses NuScenesRawLogMocker to flatten nuScenes into mock raw frames."
    ),
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="build_scenes",
            name="Build scenes",
            order=0,
            job_type=JobType.BUILD_SCENES,
            default_params={
                "build_assets": True,
                "build_world_state": False,
                "sampling": {
                    "missing_channel_policy": "keep_with_warning",
                },
            },
            outputs=_BUILD_SCENES_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="register_scene",
            name="Register scenes",
            order=1,
            job_type=JobType.REGISTER_SCENE,
            depends_on_pipeline_task_ids=["build_scenes"],
            default_params={
                "replace_existing": True,
            },
            outputs=_REGISTER_SCENE_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_scene",
            name="Validate scene",
            order=2,
            job_type=JobType.VALIDATE_SCENE,
            depends_on_pipeline_task_ids=["register_scene"],
            outputs=_VALIDATE_SCENE_OUTPUTS,
            quality_rules=_VALIDATE_SCENE_QUALITY_RULES,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_scene",
            name="Profile scene",
            order=3,
            job_type=JobType.PROFILE_SCENE,
            depends_on_pipeline_task_ids=["register_scene"],
            optional=True,
            outputs=_PROFILE_SCENE_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="build_scene_index",
            name="Build scene index",
            order=4,
            job_type=JobType.BUILD_SCENE_INDEX,
            depends_on_pipeline_task_ids=["register_scene"],
            outputs=_BUILD_SCENE_INDEX_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="build_dataset_manifest",
            name="Build dataset manifest",
            order=5,
            job_type=JobType.BUILD_DATASET_MANIFEST,
            depends_on_pipeline_task_ids=["build_scene_index"],
            outputs=_BUILD_DATASET_MANIFEST_OUTPUTS,
        ),
    ],
)


SCENE_RECONSTRUCTION_PIPELINE = PipelineDefinition(
    type=PipelineType.SCENE_RECONSTRUCTION,
    name="Scene Reconstruction",
    description=(
        "Build physics-grounded scene representation from raw logs, validate/profile "
        "the scene, and export a reconstruction package."
    ),
    supported=False,
    experimental=True,
    implemented=False,
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="build_scenes",
            name="Build scenes",
            order=0,
            job_type=JobType.BUILD_SCENES,
            default_params={
                "build_assets": True,
                "build_world_state": True,
            },
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_scene",
            name="Validate scene",
            order=1,
            job_type=JobType.VALIDATE_SCENE,
            depends_on_pipeline_task_ids=["build_scenes"],
            default_params={
                "require_world_state": True,
                "require_assets": True,
            },
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_scene",
            name="Profile scene",
            order=2,
            job_type=JobType.PROFILE_SCENE,
            depends_on_pipeline_task_ids=["build_scenes"],
            default_params={
                "profile_assets": True,
                "profile_world_state": True,
            },
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="export_scene_package",
            name="Export scene package",
            order=3,
            job_type=JobType.EXPORT_SCENE_PACKAGE,
            depends_on_pipeline_task_ids=["validate_scene"],
            default_params={
                "package_type": "reconstruction",
                "include_assets": True,
                "include_world_state": True,
            },
        ),
    ],
)


SCENE_REGISTRATION_PIPELINE = PipelineDefinition(
    type=PipelineType.SCENE_REGISTRATION,
    name="Scene Registration",
    description=(
        "Register a generated, reconstructed, simulated, or re-observed scene "
        "and run basic scene-level quality checks."
    ),
    supported=False,
    experimental=True,
    implemented=False,
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="register_scene",
            name="Register scene",
            order=0,
            job_type=JobType.REGISTER_SCENE,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_scene",
            name="Validate scene",
            order=1,
            job_type=JobType.VALIDATE_SCENE,
            depends_on_pipeline_task_ids=["register_scene"],
            default_params={
                "require_assets": True,
            },
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_scene",
            name="Profile scene",
            order=2,
            job_type=JobType.PROFILE_SCENE,
            depends_on_pipeline_task_ids=["register_scene"],
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="compare_scenes",
            name="Compare scenes",
            order=3,
            job_type=JobType.COMPARE_SCENES,
            depends_on_pipeline_task_ids=["validate_scene"],
            optional=True,
        ),
    ],
)

_MINE_SCENARIOS_OUTPUTS = [
    # REF: consumed by score_scenario_readiness / exposed as pipeline outputs
    PipelineTaskOutputSpec(name="scenario_set_id", kind=_REF, source="scenario_set_id"),
    PipelineTaskOutputSpec(
        name="scenario_set_uri", kind=_REF, source="scenario_set_uri"
    ),
    PipelineTaskOutputSpec(name="mining_run_id", kind=_REF, source="mining_run_id"),
    # ARTIFACT: lineage
    PipelineTaskOutputSpec(
        name="mining_report_uri",
        kind=_ARTIFACT,
        source="report_uri",
        target="mining_report_uri",
    ),
    # SUMMARY: task-level human-readable summary
    PipelineTaskOutputSpec(
        name="candidate_count_summary",
        kind=_SUMMARY,
        source="candidate_count",
        target="candidate_count",
    ),
    PipelineTaskOutputSpec(
        name="selected_count_summary",
        kind=_SUMMARY,
        source="selected_count",
        target="selected_count",
    ),
    PipelineTaskOutputSpec(
        name="rejected_count_summary",
        kind=_SUMMARY,
        source="rejected_count",
        target="rejected_count",
    ),
    # METRIC: pipeline-level numeric contract
    PipelineTaskOutputSpec(
        name="candidate_count_metric",
        kind=_METRIC,
        source="candidate_count",
        target="candidate_count",
    ),
    PipelineTaskOutputSpec(
        name="selected_count_metric",
        kind=_METRIC,
        source="selected_count",
        target="selected_count",
    ),
    PipelineTaskOutputSpec(
        name="rejected_count_metric",
        kind=_METRIC,
        source="rejected_count",
        target="rejected_count",
    ),
]

_SCORE_SCENARIO_READINESS_OUTPUTS = [
    # REF
    PipelineTaskOutputSpec(
        name="readiness_run_id", kind=_REF, source="readiness_run_id"
    ),
    # ARTIFACT
    PipelineTaskOutputSpec(
        name="readiness_report_uri",
        kind=_ARTIFACT,
        source="readiness_report_uri",
    ),
    # SUMMARY
    PipelineTaskOutputSpec(
        name="scored_scene_count_summary",
        kind=_SUMMARY,
        source="scored_scene_count",
        target="scored_scene_count",
    ),
    PipelineTaskOutputSpec(
        name="ready_count_summary",
        kind=_SUMMARY,
        source="ready_count",
        target="ready_count",
    ),
    PipelineTaskOutputSpec(
        name="warning_count_summary",
        kind=_SUMMARY,
        source="warning_count",
        target="warning_count",
    ),
    PipelineTaskOutputSpec(
        name="blocked_count_summary",
        kind=_SUMMARY,
        source="blocked_count",
        target="blocked_count",
    ),
    PipelineTaskOutputSpec(
        name="average_score_summary",
        kind=_SUMMARY,
        source="average_score",
        target="average_score",
    ),
    # METRIC
    PipelineTaskOutputSpec(
        name="scored_scene_count_metric",
        kind=_METRIC,
        source="scored_scene_count",
        target="scored_scene_count",
    ),
    PipelineTaskOutputSpec(
        name="ready_count_metric",
        kind=_METRIC,
        source="ready_count",
        target="ready_count",
    ),
    PipelineTaskOutputSpec(
        name="warning_count_metric",
        kind=_METRIC,
        source="warning_count",
        target="warning_count",
    ),
    PipelineTaskOutputSpec(
        name="blocked_count_metric",
        kind=_METRIC,
        source="blocked_count",
        target="blocked_count",
    ),
    PipelineTaskOutputSpec(
        name="average_score_metric",
        kind=_METRIC,
        source="average_score",
        target="average_score",
    ),
    # Optional: small downstream refs
    PipelineTaskOutputSpec(
        name="top_scene_ids",
        kind=_REF,
        source="top_scene_ids",
    ),
]


SCENARIO_CURATION_PIPELINE = PipelineDefinition(
    type=PipelineType.SCENARIO_CURATION,
    name="Scenario Curation",
    description=(
        "Mine scenario candidates from scenes and score their reconstruction "
        "or evaluation readiness."
    ),
    supported=True,
    experimental=True,
    implemented=True,
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="mine_scenarios",
            name="Mine scenarios",
            order=0,
            job_type=JobType.MINE_SCENARIOS,
            outputs=_MINE_SCENARIOS_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="score_scenario_readiness",
            name="Score scenario readiness",
            order=1,
            job_type=JobType.SCORE_SCENARIO_READINESS,
            depends_on_pipeline_task_ids=["mine_scenarios"],
            outputs=_SCORE_SCENARIO_READINESS_OUTPUTS,
        ),
    ],
)


GENERATED_DATASET_PREPARATION_PIPELINE = PipelineDefinition(
    type=PipelineType.GENERATED_DATASET_PREPARATION,
    name="Generated Dataset Preparation",
    description=(
        "Prepare generated or reconstructed scenes as a dataset version, "
        "optionally auto-label scenes, check distribution, and export the dataset."
    ),
    supported=False,
    experimental=True,
    implemented=False,
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="register_scene",
            name="Register scene",
            order=0,
            job_type=JobType.REGISTER_SCENE,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="compare_scenes",
            name="Compare scenes",
            order=1,
            job_type=JobType.COMPARE_SCENES,
            depends_on_pipeline_task_ids=["register_scene"],
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="auto_label_scene",
            name="Auto-label scene",
            order=2,
            job_type=JobType.AUTO_LABEL_SCENE,
            depends_on_pipeline_task_ids=["register_scene"],
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="build_dataset_manifest",
            name="Build dataset manifest",
            order=3,
            job_type=JobType.BUILD_DATASET_MANIFEST,
            depends_on_pipeline_task_ids=["register_scene"],
        ),
        PipelineTaskDefinition(
            pipeline_task_id="check_distribution",
            name="Check distribution",
            order=4,
            job_type=JobType.CHECK_DISTRIBUTION,
            depends_on_pipeline_task_ids=["build_dataset_manifest"],
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="export_dataset",
            name="Export dataset",
            order=5,
            job_type=JobType.EXPORT_DATASET,
            depends_on_pipeline_task_ids=["build_dataset_manifest"],
        ),
    ],
)


DETECTION_EVALUATION_PIPELINE = PipelineDefinition(
    type=PipelineType.DETECTION_EVALUATION,
    name="Detection Evaluation",
    description="Run detection prediction and evaluate detection metrics on a dataset.",
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="predict_detection",
            name="Predict detection",
            order=0,
            job_type=JobType.PREDICT_DETECTION,
            default_params={
                "inference_backend": "mock",
            },
            outputs=_PREDICT_DETECTION_OUTPUTS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="evaluate_detection",
            name="Evaluate detection",
            order=1,
            job_type=JobType.EVALUATE_DETECTION,
            depends_on_pipeline_task_ids=["predict_detection"],
            default_params={
                "evaluator_id": "center-distance",
                "match_distance_m": 2.0,
            },
            outputs=_EVALUATE_DETECTION_OUTPUTS,
        ),
    ],
)


BUILTIN_PIPELINE_DEFINITIONS = [
    DATASET_SCENE_INGESTION_PIPELINE,
    RAW_LOG_SCENE_BUILDING_PIPELINE,
    SCENE_RECONSTRUCTION_PIPELINE,
    SCENE_REGISTRATION_PIPELINE,
    SCENARIO_CURATION_PIPELINE,
    GENERATED_DATASET_PREPARATION_PIPELINE,
    DETECTION_EVALUATION_PIPELINE,
]


def create_builtin_pipeline_definition_registry() -> PipelineDefinitionRegistry:
    return PipelineDefinitionRegistry(BUILTIN_PIPELINE_DEFINITIONS)


_BUILTIN_REGISTRY: PipelineDefinitionRegistry | None = None


def get_pipeline_definition(
    pipeline_type: PipelineType,
) -> PipelineDefinition:
    global _BUILTIN_REGISTRY
    if _BUILTIN_REGISTRY is None:
        _BUILTIN_REGISTRY = create_builtin_pipeline_definition_registry()
    return _BUILTIN_REGISTRY.get(pipeline_type)
