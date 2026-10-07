from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.artifacts.schemas import ArtifactBackend
from sceneops_core.constants.tasks import PIPELINE_QUEUE, JOB_QUEUE


class StorageSettings(BaseModel):
    """Shared base for any storage backend configuration."""

    backend: ArtifactBackend = ArtifactBackend.LOCAL
    # Subclasses define their own default for root_uri.
    root_uri: str
    endpoint_url: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


class ArtifactSettings(StorageSettings):
    # Local: /data/artifacts
    # Object storage: s3://sceneops/artifacts
    root_uri: str = "/data/artifacts"

    dataset_prefix: str = "datasets"
    run_prefix: str = "runs"
    model_prefix: str = "models"
    analytics_prefix: str = "analytical"
    robot_run_prefix: str = "robot_runs"
    observation_payload_prefix: str = "observation_payloads"
    label_prefix: str = "labels"

    # Unused legacy fields — kept for backward compatibility only.
    bucket: str | None = None
    prefix: str | None = None

    @property
    def dataset_root_uri(self) -> str:
        return join_uri(self.root_uri, self.dataset_prefix)

    @property
    def run_root_uri(self) -> str:
        return join_uri(self.root_uri, self.run_prefix)

    @property
    def robot_run_root_uri(self) -> str:
        return join_uri(self.root_uri, self.robot_run_prefix)

    @property
    def observation_payload_root_uri(self) -> str:
        return join_uri(self.root_uri, self.observation_payload_prefix)

    @property
    def label_root_uri(self) -> str:
        return join_uri(self.root_uri, self.label_prefix)

    @property
    def model_root_uri(self) -> str:
        return join_uri(self.root_uri, self.model_prefix)

    @property
    def analytics_root_uri(self) -> str:
        return join_uri(self.root_uri, self.analytics_prefix)


class InputSourceSettings(StorageSettings):
    """Configuration for the read-only external input area (label documents,
    caller-supplied files).

    Separate from ArtifactSettings so that external input data and generated
    artifacts can be configured, rooted, and backed independently. Absolute
    input URIs are used as given; the root only anchors relative ones.

    Local:         /data/inputs
    Object storage: s3://sceneops/inputs
    """

    root_uri: str = "/data/inputs"


class WorkerRuntimeSettings(BaseModel):
    worker_id: str = "local-worker"


class CelerySettings(BaseModel):
    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/1"

    pipeline_queue: str = PIPELINE_QUEUE
    job_queue: str = JOB_QUEUE
    task_default_queue: str = JOB_QUEUE

    worker_prefetch_multiplier: int = 1
    task_acks_late: bool = True
    task_reject_on_worker_lost: bool = True


class ExecutionSettings(BaseModel):
    # Celery carries both Job execution (job queue) and the orchestration steps of
    # PipelineRuns (pipeline queue), which only ever submit Jobs.
    celery: CelerySettings = Field(default_factory=CelerySettings)


def join_uri(root: str, *parts: str) -> str:
    normalized_root = root.rstrip("/")
    normalized_parts = [part.strip("/") for part in parts if part.strip("/")]
    if not normalized_parts:
        return normalized_root
    return "/".join([normalized_root, *normalized_parts])
