from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sceneops_core.config import (
    ArtifactSettings,
    ExecutionSettings,
)
from sceneops_core.robots.artifact_lifecycle import (
    DEFAULT_ORPHAN_GRACE_SECONDS,
    DEFAULT_PENDING_GRACE_SECONDS,
)
from sceneops_core.robots.registration_failures import DEFAULT_STALL_THRESHOLD_SECONDS


class ReconcilerSettings(BaseModel):
    """``python -m app.domains.robots.reconciliation`` (ADR-008 §5.3)."""

    # Inactivity after which a REGISTER_ROBOT_RUN Job is a stall candidate; the
    # default's derivation is documented at DEFAULT_STALL_THRESHOLD_SECONDS
    # (sceneops_core.robots.registration_failures).
    stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS


class ArtifactLifecycleSettings(BaseModel):
    """``python -m app.domains.robots.artifact_lifecycle`` (ADR-008 §6.2)."""

    # An unreferenced object younger than this is protected as a possible
    # in-flight write (PN-1).
    pending_grace_seconds: float = DEFAULT_PENDING_GRACE_SECONDS
    # An unreferenced, unprotected object must also be this old to be an
    # orphan candidate; it is pending in between.
    orphan_grace_seconds: float = DEFAULT_ORPHAN_GRACE_SECONDS


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env.local", ".env"],
        env_prefix="SCENEOPS_API_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    artifact: ArtifactSettings = Field(default_factory=ArtifactSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    reconciler: ReconcilerSettings = Field(default_factory=ReconcilerSettings)
    artifact_lifecycle: ArtifactLifecycleSettings = Field(
        default_factory=ArtifactLifecycleSettings
    )


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()
