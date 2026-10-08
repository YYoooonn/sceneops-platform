"""IntegrationRequest / IntegrationResult: the execution contract for one
interoperability runtime that runs outside the main SceneOps runtime (e.g.
the isolated LeRobot EXPORT, ``integrations/lerobot``).

::

    SceneOps canonical artifact(s)
           |
           v
    Integration Runtime      <-- IntegrationRequest in, IntegrationResult out
           |
           v
    External dataset representation

The contract adds no DB model, job type or queue and depends on nothing
SDK-specific, so it imports unchanged from the workspace venv and from any
isolated integration environment. It carries no credentials: runtime
configuration crosses the process boundary through environment variables
or mounted config.

Three reference kinds stay separate: ``CanonicalDatasetRef`` names a
SceneOps DatasetVersion, ``ArtifactRef`` a concrete SceneOps artifact, and
``ExternalDatasetRef`` an external dataset location. Direction is owned by
``IntegrationRequest.operation``.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.artifacts.schemas import ArtifactRef
from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from .external import ExternalDatasetRef

from .enums import IntegrationOperation


class CanonicalDatasetRef(SceneOpsBaseModel):
    """The SceneOps-side identity for one integration-runtime execution:
    which DatasetVersion, nothing else. Which concrete canonical artifacts
    are read or produced is a separate concern
    (``IntegrationRequest.canonical_inputs`` /
    ``IntegrationResult.produced_artifacts``)."""

    dataset_id: str
    dataset_version: str


class IntegrationRequest(SceneOpsBaseModel):
    """Everything one integration-runtime execution needs as input.

    ``external_ref`` is the export target. ``canonical_inputs`` names every
    canonical artifact the execution reads, keyed by a runtime-local name
    (e.g. ``"learning_manifest"``); an export always reads at least one.
    ``config`` is opaque, integration-specific configuration.
    """

    operation: IntegrationOperation
    external_ref: ExternalDatasetRef
    canonical_ref: CanonicalDatasetRef
    canonical_inputs: dict[str, ArtifactRef] = Field(default_factory=dict)

    config: JsonDict = Field(default_factory=dict)
    metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_export_has_canonical_inputs(self) -> IntegrationRequest:
        if self.operation is IntegrationOperation.EXPORT and not self.canonical_inputs:
            raise ValueError(
                "EXPORT requires at least one canonical_inputs entry: "
                "an export runtime needs an already-existing canonical "
                "artifact (e.g. a learning-data export manifest) to read"
            )
        return self


class IntegrationResult(SceneOpsBaseModel):
    """What one integration-runtime execution reports on success. Failure
    is a non-zero exit code plus stderr, never a field here.

    ``produced_artifacts`` lists canonical artifacts the execution wrote;
    the main platform alone turns them into ArtifactRecords. For an export
    it is normally empty: the external output is identified by
    ``external_ref``.
    """

    operation: IntegrationOperation
    external_ref: ExternalDatasetRef
    canonical_ref: CanonicalDatasetRef

    produced_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    result_metadata: JsonDict = Field(default_factory=dict)


__all__ = ["CanonicalDatasetRef", "IntegrationRequest", "IntegrationResult"]
