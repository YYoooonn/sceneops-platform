from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.dependencies import DbSessionDep
from sceneops_db.repositories.artifacts import ArtifactRepository
from sceneops_db.repositories.datasets import (
    DatasetRepository,
    DatasetVersionRepository,
)
from sceneops_db.repositories.episodes import EpisodeRepository
from sceneops_db.repositories.evaluations import EvaluationRunRepository
from sceneops_db.repositories.executions import ExecutionRecordRepository
from sceneops_db.repositories.inference import InferenceRunRepository
from sceneops_db.repositories.jobs import JobEventRepository, JobRepository
from sceneops_db.repositories.model_registry import (
    ModelRepository,
    ModelVersionRepository,
)
from sceneops_db.repositories.pipelines import (
    PipelineRunRepository,
    PipelineTaskRunRepository,
)
from sceneops_db.repositories.robots import (
    MissionRepository,
    RobotRepository,
    RobotRunRepository,
    RobotStateRepository,
)
from sceneops_db.repositories.scenarios import (
    ScenarioRunRepository,
    ScenarioSetRepository,
)
from sceneops_db.repositories.scenes import SceneRepository, SceneRunRepository

from sceneops_db.postgres.artifacts import PostgresArtifactRefRepository
from sceneops_db.postgres.datasets import (
    PostgresDatasetRepository,
    PostgresDatasetVersionRepository,
)
from sceneops_db.postgres.episodes import PostgresEpisodeRepository
from sceneops_db.postgres.evaluations import PostgresEvaluationRunRepository
from sceneops_db.postgres.executions import PostgresExecutionRecordRepository
from sceneops_db.postgres.inference import PostgresInferenceRunRepository
from sceneops_db.postgres.jobs import PostgresJobEventRepository, PostgresJobRepository
from sceneops_db.postgres.model_registry import (
    PostgresModelRepository,
    PostgresModelVersionRepository,
)
from sceneops_db.postgres.pipelines import (
    PostgresPipelineRunRepository,
    PostgresPipelineTaskRunRepository,
)
from sceneops_db.postgres.robots import (
    PostgresMissionRepository,
    PostgresRobotRepository,
    PostgresRobotRunRepository,
    PostgresRobotStateRepository,
)
from sceneops_db.postgres.scenarios import (
    PostgresScenarioRunRepository,
    PostgresScenarioSetRepository,
)
from sceneops_db.postgres.scenes import (
    PostgresSceneRepository,
    PostgresSceneRunRepository,
)


# --- platform: jobs ---


def get_job_repository(session: DbSessionDep) -> JobRepository:
    return PostgresJobRepository(session)


def get_job_event_repository(session: DbSessionDep) -> JobEventRepository:
    return PostgresJobEventRepository(session)


JobRepositoryDep = Annotated[JobRepository, Depends(get_job_repository)]
JobEventRepositoryDep = Annotated[JobEventRepository, Depends(get_job_event_repository)]


# --- platform: pipelines ---


def get_pipeline_run_repository(session: DbSessionDep) -> PipelineRunRepository:
    return PostgresPipelineRunRepository(session)


def get_pipeline_task_run_repository(
    session: DbSessionDep,
) -> PipelineTaskRunRepository:
    return PostgresPipelineTaskRunRepository(session)


PipelineRunRepositoryDep = Annotated[
    PipelineRunRepository, Depends(get_pipeline_run_repository)
]
PipelineTaskRunRepositoryDep = Annotated[
    PipelineTaskRunRepository, Depends(get_pipeline_task_run_repository)
]


# --- platform: executions ---


def get_execution_record_repository(session: DbSessionDep) -> ExecutionRecordRepository:
    return PostgresExecutionRecordRepository(session)


ExecutionRecordRepositoryDep = Annotated[
    ExecutionRecordRepository, Depends(get_execution_record_repository)
]


# --- platform: artifacts ---


def get_artifact_repository(session: DbSessionDep) -> ArtifactRepository:
    return PostgresArtifactRefRepository(session)


ArtifactRepositoryDep = Annotated[ArtifactRepository, Depends(get_artifact_repository)]


# --- domains: datasets ---


def get_dataset_repository(session: DbSessionDep) -> DatasetRepository:
    return PostgresDatasetRepository(session)


def get_dataset_version_repository(session: DbSessionDep) -> DatasetVersionRepository:
    return PostgresDatasetVersionRepository(session)


DatasetRepositoryDep = Annotated[DatasetRepository, Depends(get_dataset_repository)]
DatasetVersionRepositoryDep = Annotated[
    DatasetVersionRepository, Depends(get_dataset_version_repository)
]


# --- domains: scenes ---


def get_scene_repository(session: DbSessionDep) -> SceneRepository:
    return PostgresSceneRepository(session)


def get_scene_run_repository(session: DbSessionDep) -> SceneRunRepository:
    return PostgresSceneRunRepository(session)


SceneRepositoryDep = Annotated[SceneRepository, Depends(get_scene_repository)]
SceneRunRepositoryDep = Annotated[SceneRunRepository, Depends(get_scene_run_repository)]


# --- domains: episodes ---


def get_episode_repository(session: DbSessionDep) -> EpisodeRepository:
    return PostgresEpisodeRepository(session)


EpisodeRepositoryDep = Annotated[EpisodeRepository, Depends(get_episode_repository)]


# --- domains: scenarios ---


def get_scenario_set_repository(session: DbSessionDep) -> ScenarioSetRepository:
    return PostgresScenarioSetRepository(session)


def get_scenario_run_repository(session: DbSessionDep) -> ScenarioRunRepository:
    return PostgresScenarioRunRepository(session)


ScenarioSetRepositoryDep = Annotated[
    ScenarioSetRepository, Depends(get_scenario_set_repository)
]
ScenarioRunRepositoryDep = Annotated[
    ScenarioRunRepository, Depends(get_scenario_run_repository)
]


# --- domains: models ---


def get_model_repository(session: DbSessionDep) -> ModelRepository:
    return PostgresModelRepository(session)


def get_model_version_repository(session: DbSessionDep) -> ModelVersionRepository:
    return PostgresModelVersionRepository(session)


ModelRepositoryDep = Annotated[ModelRepository, Depends(get_model_repository)]
ModelVersionRepositoryDep = Annotated[
    ModelVersionRepository, Depends(get_model_version_repository)
]


# --- domains: inference ---


def get_inference_run_repository(session: DbSessionDep) -> InferenceRunRepository:
    return PostgresInferenceRunRepository(session)


InferenceRunRepositoryDep = Annotated[
    InferenceRunRepository, Depends(get_inference_run_repository)
]


# --- domains: evaluations ---


def get_evaluation_run_repository(session: DbSessionDep) -> EvaluationRunRepository:
    return PostgresEvaluationRunRepository(session)


EvaluationRunRepositoryDep = Annotated[
    EvaluationRunRepository, Depends(get_evaluation_run_repository)
]


# --- domains: robots ---


def get_robot_repository(session: DbSessionDep) -> RobotRepository:
    return PostgresRobotRepository(session)


def get_robot_run_repository(session: DbSessionDep) -> RobotRunRepository:
    return PostgresRobotRunRepository(session)


def get_mission_repository(session: DbSessionDep) -> MissionRepository:
    return PostgresMissionRepository(session)


def get_robot_state_repository(session: DbSessionDep) -> RobotStateRepository:
    return PostgresRobotStateRepository(session)


RobotRepositoryDep = Annotated[RobotRepository, Depends(get_robot_repository)]
RobotRunRepositoryDep = Annotated[RobotRunRepository, Depends(get_robot_run_repository)]
MissionRepositoryDep = Annotated[MissionRepository, Depends(get_mission_repository)]
RobotStateRepositoryDep = Annotated[
    RobotStateRepository, Depends(get_robot_state_repository)
]
