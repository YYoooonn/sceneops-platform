from __future__ import annotations

from sceneops_core.jobs.schemas import JobType
from sceneops_core.pipelines.registry import PipelineDefinitionRegistry
from sceneops_core.pipelines.schemas import (
    PipelineDefinition,
    PipelineTaskDefinition,
    PipelineType,
)


DATASET_SCENE_INGESTION_PIPELINE = PipelineDefinition(
    type=PipelineType.DATASET_SCENE_INGESTION,
    name="Dataset Scene Ingestion",
    description=(
        "Import existing scene-aware datasets such as nuScenes, Waymo, or KITTI "
        "into SceneOps scene manifests, then build a dataset manifest."
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
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_scene",
            name="Validate scene",
            order=1,
            job_type=JobType.VALIDATE_SCENE,
            depends_on_pipeline_task_ids=["ingest_scenes"],
            default_params={
                "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
            },
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_scene",
            name="Profile scene",
            order=2,
            job_type=JobType.PROFILE_SCENE,
            depends_on_pipeline_task_ids=["ingest_scenes"],
            default_params={
                "profile_samples": True,
                "profile_assets": True,
            },
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="build_dataset_manifest",
            name="Build dataset manifest",
            order=3,
            job_type=JobType.BUILD_DATASET_MANIFEST,
            depends_on_pipeline_task_ids=["ingest_scenes"],
        ),
    ],
)


RAW_LOG_SCENE_BUILDING_PIPELINE = PipelineDefinition(
    type=PipelineType.RAW_LOG_SCENE_BUILDING,
    name="Raw Log Scene Building",
    description=(
        "Build SceneOps scene manifests from raw logs or raw sensor streams, "
        "then aggregate them into a dataset manifest."
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
            },
        ),
        PipelineTaskDefinition(
            pipeline_task_id="validate_scene",
            name="Validate scene",
            order=1,
            job_type=JobType.VALIDATE_SCENE,
            depends_on_pipeline_task_ids=["build_scenes"],
            default_params={
                "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"],
            },
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="profile_scene",
            name="Profile scene",
            order=2,
            job_type=JobType.PROFILE_SCENE,
            depends_on_pipeline_task_ids=["build_scenes"],
            optional=True,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="build_dataset_manifest",
            name="Build dataset manifest",
            order=3,
            job_type=JobType.BUILD_DATASET_MANIFEST,
            depends_on_pipeline_task_ids=["build_scenes"],
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


SCENARIO_CURATION_PIPELINE = PipelineDefinition(
    type=PipelineType.SCENARIO_CURATION,
    name="Scenario Curation",
    description=(
        "Mine scenario candidates from a dataset and score their reconstruction "
        "or evaluation readiness."
    ),
    tasks=[
        PipelineTaskDefinition(
            pipeline_task_id="mine_scenarios",
            name="Mine scenarios",
            order=0,
            job_type=JobType.MINE_SCENARIOS,
        ),
        PipelineTaskDefinition(
            pipeline_task_id="score_scenario_readiness",
            name="Score scenario readiness",
            order=1,
            job_type=JobType.SCORE_SCENARIO_READINESS,
            depends_on_pipeline_task_ids=["mine_scenarios"],
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
