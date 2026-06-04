from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession

from sceneops_storage import ArtifactStore, create_artifact_store

from sceneops_worker.config import WorkerSettings, get_settings
from sceneops_worker.core.context import RunStores, WorkerContext
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


@lru_cache
def _get_artifact_store(settings: WorkerSettings) -> ArtifactStore:
    return create_artifact_store(settings.artifact)


def create_worker_context(
    session: AsyncSession,
    *,
    settings: WorkerSettings | None = None,
    worker_id: str | None = None,
) -> WorkerContext:
    settings = settings or get_settings()

    artifact_store = _get_artifact_store(settings)

    return WorkerContext(
        worker_id=worker_id or settings.worker_id,
        settings=settings,
        artifact_store=artifact_store,
        dataset_artifact_store=DatasetArtifactStore(
            artifact_store=artifact_store,
            dataset_root_uri=settings.dataset_root_uri,
        ),
        run_artifact_store=RunArtifactStore(
            artifact_store=artifact_store,
            runs_root_uri=settings.run_root_uri,
        ),
        job_store=JobStore(session),
        job_event_store=JobEventStore(session),
        pipeline_store=PipelineStore(session),
        dataset_store=DatasetStore(session),
        scene_store=SceneStore(session),
        scenario_store=ScenarioStore(session),
        model_store=ModelStore(session),
        artifact_record_store=ArtifactRecordStore(session),
        runs=RunStores(
            inference=InferenceRunStore(session),
            evaluations=EvaluationRunStore(session),
            labels=LabelRunStore(session),
            scene_runs=SceneRunStore(session),
            dataset_runs=DatasetRunStore(session),
        ),
        default_dataset_id=settings.default_dataset_id,
        default_dataset_version=settings.default_dataset_version,
    )
