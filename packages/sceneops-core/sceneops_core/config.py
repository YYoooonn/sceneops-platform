from __future__ import annotations

from pydantic import BaseModel, Field

from sceneops_core.executions.schemas import ExecutionBackend
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
    def model_root_uri(self) -> str:
        return join_uri(self.root_uri, self.model_prefix)

    @property
    def analytics_root_uri(self) -> str:
        return join_uri(self.root_uri, self.analytics_prefix)


class RawSourceSettings(StorageSettings):
    """Configuration for the read-only raw dataset source.

    Separate from ArtifactSettings so that raw input data and generated
    artifacts can be configured, rooted, and backed independently.

    Local:         /data/raw/nuscenes
    Object storage: s3://sceneops/raw/nuscenes
    """

    root_uri: str = "/data/raw/nuscenes"


class DefaultDatasetSettings(BaseModel):
    dataset_id: str = "nuscenes"
    dataset_version: str = "v1.0-mini"


class WorkerRuntimeSettings(BaseModel):
    worker_id: str = "local-worker"
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 10.0
    job_poll_interval_seconds: float = 1.0
    job_wait_timeout_seconds: float = 3600.0


class CelerySettings(BaseModel):
    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/1"

    pipeline_queue: str = PIPELINE_QUEUE
    job_queue: str = JOB_QUEUE
    task_default_queue: str = JOB_QUEUE

    worker_prefetch_multiplier: int = 1
    task_acks_late: bool = True
    task_reject_on_worker_lost: bool = True


class AirflowSettings(BaseModel):
    base_url: str = "http://airflow-webserver:8080"
    username: str | None = None
    password: str | None = None

    pipeline_dag_id: str = "sceneops_pipeline_run"
    job_dag_id: str = "sceneops_job_run"


class ExecutionSettings(BaseModel):
    job_backend: ExecutionBackend = ExecutionBackend.CELERY
    pipeline_backend: ExecutionBackend = ExecutionBackend.CELERY
    celery: CelerySettings = Field(default_factory=CelerySettings)
    airflow: AirflowSettings = Field(default_factory=AirflowSettings)


class IntegrationExecutionSettings(BaseModel):
    """Configuration for invoking isolated integration runtimes from the
    worker (SceneOps V2 Request 4.6/4.6A) -- explicit and swappable per
    environment, never a scattered conditional. Credentials/backend
    selection for a runtime's OWN ArtifactStore are never duplicated here
    -- they're translated from this worker's own ArtifactSettings into
    SCENEOPS_INTEGRATION_ARTIFACT__* environment variables at call time
    (apps/worker/sceneops_worker/integration_execution/{http,container}.py).

    ``nuscenes_service_url`` is the production routing config (Request
    4.6A §3/§6): the worker's ``HttpIntegrationExecutor`` reaches the
    nuScenes integration runtime as a plain internal HTTP service on the
    SceneOps network (``compose/integrations.yaml``'s ``nuscenes-
    integration`` service), resolved by service name -- never by
    controlling Docker. A future LeRobot (or other) integration would add
    its own ``<name>_service_url`` field here, not a registry/plugin
    system.

    ``nuscenes_image``/``docker_network``/``host_data_root``/
    ``io_root_uri`` remain for ``ContainerIntegrationExecutor`` (Request
    4.6), now a LOCAL/DEV-ONLY backend (``make nuscenes-container-smoke``,
    direct runtime debugging) -- not the worker's production path since
    Request 4.6A. ``host_data_root``/``io_root_uri`` exist only because
    that backend runs `docker run` from inside an already-containerized
    worker (Docker-outside-of-Docker): a sibling container's ``-v
    host:container`` mount is resolved by the Docker daemon against the
    HOST filesystem, never the calling container's own view of it, even
    though the worker already sees the same content locally. A later
    Kubernetes (or any other) executor implementing the same
    ``IntegrationExecutor`` protocol would use its own config shape --
    nothing about ``IntegrationRequest``/``IntegrationResult`` depends on
    Docker, HTTP, or any field here.
    """

    nuscenes_service_url: str = "http://nuscenes-integration:8080"

    nuscenes_image: str = "sceneops-platform/nuscenes-integration:local"
    docker_network: str | None = "sceneops-network"
    host_data_root: str | None = None
    io_root_uri: str = "/data/runs/integration-exec"


def join_uri(root: str, *parts: str) -> str:
    normalized_root = root.rstrip("/")
    normalized_parts = [part.strip("/") for part in parts if part.strip("/")]
    if not normalized_parts:
        return normalized_root
    return "/".join([normalized_root, *normalized_parts])
