from __future__ import annotations

from typing import Generic, Protocol, TypeVar, runtime_checkable

SceneBuildRequestT = TypeVar("SceneBuildRequestT", contravariant=True)
SceneBuildResultT = TypeVar("SceneBuildResultT", covariant=True)

SceneValidationRequestT = TypeVar("SceneValidationRequestT", contravariant=True)
SceneValidationResultT = TypeVar("SceneValidationResultT", covariant=True)

SceneProfileRequestT = TypeVar("SceneProfileRequestT", contravariant=True)
SceneProfileResultT = TypeVar("SceneProfileResultT", covariant=True)


@runtime_checkable
class SceneBuilder(
    Protocol,
    Generic[SceneBuildRequestT, SceneBuildResultT],
):
    """Port-like contract for building structured scenes from observations."""

    @property
    def builder_id(self) -> str:
        """Stable builder identifier, e.g. nuscenes-scene-builder."""

    async def run(
        self,
        request: SceneBuildRequestT,
    ) -> SceneBuildResultT:
        """Build scene manifests from raw observation inputs."""


@runtime_checkable
class SceneValidator(
    Protocol,
    Generic[SceneValidationRequestT, SceneValidationResultT],
):
    """Port-like contract for scene-level validation."""

    @property
    def validator_id(self) -> str:
        """Stable validator identifier, e.g. scene-manifest-validator."""

    async def run(
        self,
        request: SceneValidationRequestT,
    ) -> SceneValidationResultT:
        """Validate scene manifests and return a scene validation result."""


@runtime_checkable
class SceneProfiler(
    Protocol,
    Generic[SceneProfileRequestT, SceneProfileResultT],
):
    """Port-like contract for scene-level profiling."""

    @property
    def profiler_id(self) -> str:
        """Stable profiler identifier, e.g. scene-profile-standard."""

    async def run(
        self,
        request: SceneProfileRequestT,
    ) -> SceneProfileResultT:
        """Profile scene manifests and return a scene profile result."""
