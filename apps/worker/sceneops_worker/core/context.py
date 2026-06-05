from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_storage import ArtifactStore

from sceneops_worker.config import WorkerSettings
from sceneops_worker.datasets.artifacts import DatasetArtifactStore
from sceneops_worker.runs.artifacts import RunArtifactStore
from sceneops_worker.stores.artifacts import ArtifactRecordStore
from sceneops_worker.stores.datasets import DatasetStore
from sceneops_worker.stores.jobs import JobEventStore, JobStore
from sceneops_worker.stores.models import ModelStore
from sceneops_worker.stores.pipelines import PipelineStore
from sceneops_worker.stores.runs import (
    DatasetRunStore,
    EvaluationRunStore,
    InferenceRunStore,
    LabelRunStore,
    SceneRunStore,
)
from sceneops_worker.stores.scenarios import ScenarioStore
from sceneops_worker.stores.scenes import SceneStore


@dataclass(frozen=True)
class RunStores:
    inference: InferenceRunStore
    evaluations: EvaluationRunStore
    labels: LabelRunStore
    scene_runs: SceneRunStore
    dataset_runs: DatasetRunStore


@dataclass(frozen=True)
class WorkerContext:
    worker_id: str
    settings: WorkerSettings
    session: AsyncSession

    artifact_store: ArtifactStore
    dataset_artifact_store: DatasetArtifactStore
    run_artifact_store: RunArtifactStore

    job_store: JobStore
    job_event_store: JobEventStore
    pipeline_store: PipelineStore

    dataset_store: DatasetStore
    scene_store: SceneStore
    scenario_store: ScenarioStore
    model_store: ModelStore
    artifact_record_store: ArtifactRecordStore

    runs: RunStores

    default_dataset_id: str
    default_dataset_version: str

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
