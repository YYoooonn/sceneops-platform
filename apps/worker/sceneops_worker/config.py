from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import (
    ArtifactSettings,
    ExecutionSettings,
    InputSourceSettings,
    WorkerRuntimeSettings,
)
from sceneops_acquisition.lifecycle.vocabulary import (
    DEFAULT_ORPHAN_GRACE_SECONDS,
    DEFAULT_PENDING_GRACE_SECONDS,
)
from sceneops_acquisition.registration_failures import DEFAULT_STALL_THRESHOLD_SECONDS


class ReconcilerSettings(BaseModel):
    """``sceneops-worker acquisition reconcile`` (ADR-008 §5.3)."""

    # Inactivity after which a REGISTER_ROBOT_RUN Job is a stall candidate; the
    # default's derivation is documented at DEFAULT_STALL_THRESHOLD_SECONDS
    # (sceneops_acquisition.registration_failures).
    stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS


class ArtifactLifecycleSettings(BaseModel):
    """``sceneops-worker acquisition artifact-lifecycle`` (ADR-008 §6.2)."""

    # An unreferenced object younger than this is protected as a possible
    # in-flight write (PN-1).
    pending_grace_seconds: float = DEFAULT_PENDING_GRACE_SECONDS
    # An unreferenced, unprotected object must also be this old to be an
    # orphan candidate; it is pending in between.
    orphan_grace_seconds: float = DEFAULT_ORPHAN_GRACE_SECONDS


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_prefix="SCENEOPS_WORKER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)
    input_source: InputSourceSettings = Field(default_factory=InputSourceSettings)
    runtime: WorkerRuntimeSettings = Field(
        default_factory=WorkerRuntimeSettings,
    )
    execution: ExecutionSettings = Field(
        default_factory=ExecutionSettings,
    )
    reconciler: ReconcilerSettings = Field(default_factory=ReconcilerSettings)
    artifact_lifecycle: ArtifactLifecycleSettings = Field(
        default_factory=ArtifactLifecycleSettings
    )

    @property
    def artifact_root_uri(self) -> str:
        return self.artifact.root_uri

    @property
    def dataset_root_uri(self) -> str:
        return self.artifact.dataset_root_uri

    @property
    def run_root_uri(self) -> str:
        return self.artifact.run_root_uri

    @property
    def observation_payload_root_uri(self) -> str:
        return self.artifact.observation_payload_root_uri

    @property
    def label_root_uri(self) -> str:
        return self.artifact.label_root_uri

    @property
    def analytics_root_uri(self) -> str:
        return self.artifact.analytics_root_uri

    @property
    def worker_id(self) -> str:
        return self.runtime.worker_id


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()
