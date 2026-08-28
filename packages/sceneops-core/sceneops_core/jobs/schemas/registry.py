from __future__ import annotations

from typing import TypeAlias

from sceneops_core.common.schemas import JsonDict

from .enums import JobType
from .params import (
    AutoLabelDatasetJobParams,
    AutoLabelSceneJobParams,
    BaseJobParams,
    BuildDatasetManifestJobParams,
    BuildSceneIndexJobParams,
    BuildScenesJobParams,
    CheckDistributionJobParams,
    CompareScenesJobParams,
    EvaluateDetectionJobParams,
    ExportAnalyticsSnapshotJobParams,
    ExportDatasetJobParams,
    ExportScenePackageJobParams,
    IngestScenesJobParams,
    MineScenariosJobParams,
    IngestRobotStatesJobParams,
    PredictDetectionJobParams,
    ProfileSceneJobParams,
    RegisterSceneJobParams,
    ScoreScenarioReadinessJobParams,
    ValidateSceneJobParams,
)
from .results import (
    AutoLabelDatasetJobResult,
    AutoLabelSceneJobResult,
    BaseJobResult,
    BuildDatasetManifestJobResult,
    BuildSceneIndexJobResult,
    BuildScenesJobResult,
    CheckDistributionJobResult,
    CompareScenesJobResult,
    EvaluateDetectionJobResult,
    ExportAnalyticsSnapshotJobResult,
    ExportDatasetJobResult,
    ExportScenePackageJobResult,
    IngestRobotStatesJobResult,
    IngestScenesJobResult,
    MineScenariosJobResult,
    PredictDetectionJobResult,
    ProfileSceneJobResult,
    RegisterSceneJobResult,
    ScoreScenarioReadinessJobResult,
    ValidateSceneJobResult,
)

JobParamsModel: TypeAlias = type[BaseJobParams]
JobResultModel: TypeAlias = type[BaseJobResult]

JOB_PARAM_SCHEMA_BY_TYPE: dict[JobType, JobParamsModel] = {
    JobType.INGEST_SCENES: IngestScenesJobParams,
    JobType.BUILD_SCENES: BuildScenesJobParams,
    JobType.BUILD_DATASET_MANIFEST: BuildDatasetManifestJobParams,
    JobType.BUILD_SCENE_INDEX: BuildSceneIndexJobParams,
    JobType.VALIDATE_SCENE: ValidateSceneJobParams,
    JobType.PROFILE_SCENE: ProfileSceneJobParams,
    JobType.REGISTER_SCENE: RegisterSceneJobParams,
    JobType.COMPARE_SCENES: CompareScenesJobParams,
    JobType.AUTO_LABEL_SCENE: AutoLabelSceneJobParams,
    JobType.EXPORT_SCENE_PACKAGE: ExportScenePackageJobParams,
    JobType.MINE_SCENARIOS: MineScenariosJobParams,
    JobType.SCORE_SCENARIO_READINESS: ScoreScenarioReadinessJobParams,
    JobType.AUTO_LABEL_DATASET: AutoLabelDatasetJobParams,
    JobType.CHECK_DISTRIBUTION: CheckDistributionJobParams,
    JobType.EXPORT_DATASET: ExportDatasetJobParams,
    JobType.EXPORT_ANALYTICS_SNAPSHOT: ExportAnalyticsSnapshotJobParams,
    JobType.PREDICT_DETECTION: PredictDetectionJobParams,
    JobType.EVALUATE_DETECTION: EvaluateDetectionJobParams,
    JobType.INGEST_ROBOT_STATES: IngestRobotStatesJobParams,
}

JOB_RESULT_SCHEMA_BY_TYPE: dict[JobType, JobResultModel] = {
    JobType.INGEST_SCENES: IngestScenesJobResult,
    JobType.BUILD_SCENES: BuildScenesJobResult,
    JobType.BUILD_DATASET_MANIFEST: BuildDatasetManifestJobResult,
    JobType.BUILD_SCENE_INDEX: BuildSceneIndexJobResult,
    JobType.VALIDATE_SCENE: ValidateSceneJobResult,
    JobType.PROFILE_SCENE: ProfileSceneJobResult,
    JobType.REGISTER_SCENE: RegisterSceneJobResult,
    JobType.COMPARE_SCENES: CompareScenesJobResult,
    JobType.AUTO_LABEL_SCENE: AutoLabelSceneJobResult,
    JobType.EXPORT_SCENE_PACKAGE: ExportScenePackageJobResult,
    JobType.MINE_SCENARIOS: MineScenariosJobResult,
    JobType.SCORE_SCENARIO_READINESS: ScoreScenarioReadinessJobResult,
    JobType.AUTO_LABEL_DATASET: AutoLabelDatasetJobResult,
    JobType.CHECK_DISTRIBUTION: CheckDistributionJobResult,
    JobType.EXPORT_DATASET: ExportDatasetJobResult,
    JobType.EXPORT_ANALYTICS_SNAPSHOT: ExportAnalyticsSnapshotJobResult,
    JobType.PREDICT_DETECTION: PredictDetectionJobResult,
    JobType.EVALUATE_DETECTION: EvaluateDetectionJobResult,
    JobType.INGEST_ROBOT_STATES: IngestRobotStatesJobResult,
}


def parse_job_params(job_type: JobType, params: JsonDict) -> BaseJobParams:
    schema = JOB_PARAM_SCHEMA_BY_TYPE.get(job_type)
    if schema is None:
        raise ValueError(f"Unsupported job type: {job_type}")
    return schema.model_validate(params)


def parse_job_result(job_type: JobType, result: JsonDict) -> BaseJobResult:
    schema = JOB_RESULT_SCHEMA_BY_TYPE.get(job_type)
    if schema is None:
        raise ValueError(f"Unsupported job type: {job_type}")
    return schema.model_validate(result)
