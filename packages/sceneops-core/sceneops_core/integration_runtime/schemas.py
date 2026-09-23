"""IntegrationRequest / IntegrationResult: the frozen execution contract for
running one external dataset integration outside the main SceneOps runtime
(SceneOps V2 Request 4.1, reference semantics cleaned up in Request 4.1A).

::

    External Dataset
           |
           v
    Integration Runtime      <-- IntegrationRequest in, IntegrationResult out
           |
           v
    SceneOps Canonical Platform

This module defines the smallest useful conceptual contract for that
boundary, not a new execution system. It intentionally:

* adds no DB model, job type, queue, or RPC service -- a future container/
  CLI receives an ``IntegrationRequest`` (e.g. as a JSON file or stdin
  payload) and, on success, prints/writes an ``IntegrationResult`` the same
  way. How that payload physically crosses the boundary (argv, stdin, a
  mounted file, an env var) is a later request's concern.
* depends on nothing SDK-specific -- ``sceneops-core`` has no dependency on
  ``lerobot`` or ``nuscenes-devkit`` and never will (see
  ``tools/lerobot-integration/pyproject.toml`` for why that separation is
  load-bearing), so this contract is importable, unchanged, from both the
  main workspace venv and any isolated integration venv/container.
* carries no credentials. Runtime credentials/config (MinIO/S3 keys, DB
  DSNs, ...) cross the process boundary via environment variables /
  mounted config, exactly as ``scripts/e2e/e2e_lerobot_resolve.py`` and
  ``scripts/e2e/e2e_lerobot_export.py`` already do (``MINIO_ROOT_USER``,
  ``SCENEOPS_DATABASE_URL``, ...) -- never as a field on a serializable
  request/result object that might be logged, persisted, or round-tripped
  through a job record.
* keeps three reference kinds strictly separate rather than conflating them
  (Request 4.1A): ``CanonicalDatasetRef`` answers *which SceneOps
  DatasetVersion*, ``ArtifactRef`` (Request 2.1) answers *which concrete
  SceneOps ArtifactStore artifact*, and ``ExternalDatasetRef``
  (Request 3.2B) answers *which external dataset representation*. Request
  4.1 originally embedded a single ``ArtifactRef`` directly on
  ``CanonicalDatasetRef`` -- that conflated canonical identity with one
  particular artifact and could not express "zero or several canonical
  artifact inputs/outputs", which nuScenes ingestion already needs (it
  produces two: a raw-log manifest and a raw-log frame index). Canonical
  artifact references now live in their own explicit, direction-scoped
  dict fields (``canonical_inputs`` on the request, ``produced_artifacts``
  on the result) instead.

Direction is owned by ``IntegrationRequest.operation``
(``IntegrationOperation``), never by a separate source/export ref type --
see ``enums.py``.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from sceneops_core.artifacts.schemas import ArtifactRef
from sceneops_core.common.schemas import JsonDict, SceneOpsBaseModel
from sceneops_core.datasets.schemas.external import ExternalDatasetRef

from .enums import IntegrationOperation


class CanonicalDatasetRef(SceneOpsBaseModel):
    """The SceneOps-side *identity* for one integration-runtime execution --
    which DatasetVersion, nothing else (Request 4.1A). Never carries an
    artifact pointer: which concrete canonical artifact(s) are read or
    produced is a separate, direction-scoped concern (see
    ``IntegrationRequest.canonical_inputs`` /
    ``IntegrationResult.produced_artifacts``), because an execution may
    need zero, one, or several of them -- nuScenes ingestion alone already
    produces two (a raw-log manifest and a raw-log frame index).
    """

    dataset_id: str
    dataset_version: str


class IntegrationRequest(SceneOpsBaseModel):
    """Everything one integration-runtime execution needs as input,
    explicit and serializable -- no hidden process state (SceneOps V2
    Request 4.1 §6/§7).

    ``external_ref`` is the external system's side of the operation: the
    import source for INGEST, the export target for EXPORT (same
    ``ExternalDatasetRef`` shape either way, per Request 3.2B).
    ``canonical_ref`` is the SceneOps DatasetVersion identity.
    ``canonical_inputs`` is every canonical artifact this execution reads
    from, keyed by a runtime-local name the caller and runtime agree on
    (e.g. ``"learning_manifest"`` for a LeRobot export) -- this contract
    intentionally does not fix or register that vocabulary of names (no
    generic artifact-key registry); it is integration-specific, exactly
    like ``config``. Required non-empty for EXPORT (an export runtime
    always reads an already-existing canonical artifact); optional for
    INGEST -- nuScenes ingestion happens to need none today, but a generic
    ingest runtime may legitimately consume existing canonical context
    (calibration, schema, ...) while still producing new canonical
    artifacts, so this contract does not forbid it (Request 4.1A
    follow-up). ``config`` is opaque, integration-specific configuration
    (e.g. a serialized ``FeatureProjection`` for a LeRobot export, or
    ``max_source_sequences``/``required_channels`` for a nuScenes ingest)
    -- this contract does not know or constrain its shape, matching
    ``BuildScenesJobHandler``'s existing ``params: dict`` convention for
    format-specific adapter config.
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
    """Everything one integration-runtime execution reports back on success
    (SceneOps V2 Request 4.1 §6). Failure is reported by the runtime's own
    process exit code plus stderr, not by a field here -- the same
    convention every ``scripts/e2e/e2e_lerobot_*.py`` script already uses
    (a non-zero exit and a clear stderr message, never a caught error
    serialized as "success").

    ``produced_artifacts`` is every canonical artifact this execution
    wrote, keyed the same runtime-local way as
    ``IntegrationRequest.canonical_inputs`` (e.g.
    ``"raw_log_manifest"``/``"raw_log_frame_index"`` for a nuScenes
    ingest). The main platform remains solely responsible for turning
    these into ArtifactRecord/lineage entries -- the integration runtime
    itself never writes that DB record. For an EXPORT this is normally
    empty: the external output (e.g. a LeRobot dataset root) is already
    fully identified by ``external_ref`` and is not, by itself, something
    genuinely stored in the SceneOps ArtifactStore (Request 4.1A §5) --
    duplicating it here as an ``ArtifactRef`` would just be the same
    location under a second name.
    """

    operation: IntegrationOperation
    external_ref: ExternalDatasetRef
    canonical_ref: CanonicalDatasetRef

    produced_artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    result_metadata: JsonDict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_produced_artifacts_match_direction(self) -> IntegrationResult:
        if (
            self.operation is IntegrationOperation.INGEST
            and not self.produced_artifacts
        ):
            raise ValueError(
                "INGEST result requires at least one produced_artifacts "
                "entry: an ingest that produced nothing leaves the main "
                "platform nothing to register"
            )
        return self


__all__ = ["CanonicalDatasetRef", "IntegrationRequest", "IntegrationResult"]
