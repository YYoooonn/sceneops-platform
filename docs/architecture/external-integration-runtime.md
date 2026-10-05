# External Integration Runtime

An **integration runtime** runs SDK-bound interoperability code for one
external dataset format outside the main SceneOps runtime: a separate
process or container that the platform talks to only through a
serializable request/result contract. Today it serves one direction,
**export** (SceneOps → LeRobot).

There is no ingest runtime. Acquired data enters SceneOps only as a
registered RobotRun recording ([ADR-007](../adr/007-canonical-ingestion-architecture.md)
§29.2, I-31). An external dataset reaches SceneOps through the
dataset-acquisition tool (`tools/dataset-acquisition`), which converts it
into an L1 recording and has no SceneOps dependency; see
[Robot data ingestion](../workflows/robot-run-and-mcap.md).

## 1. Reference and runtime contracts

`sceneops_core.integration_runtime`:

```text
CanonicalDatasetRef   which SceneOps DatasetVersion (dataset_id, dataset_version);
                      never an artifact pointer
ArtifactRef           one concrete SceneOps ArtifactStore object
                      (sceneops_core.artifacts.schemas)
ExternalDatasetRef    where the runtime writes the external representation:
                      format, format_version, uri, external_name?,
                      external_revision?, checksum?
                      An integration-runtime locator only; never canonical
                      provenance, identity or fingerprint input

IntegrationOperation  EXPORT   SceneOps canonical -> ExternalDatasetRef

IntegrationRequest    operation, external_ref, canonical_ref,
                      canonical_inputs: dict[str, ArtifactRef] (non-empty for EXPORT),
                      config (opaque, integration-specific), metadata
IntegrationResult     operation, external_ref, canonical_ref (echoed),
                      produced_artifacts: dict[str, ArtifactRef], result_metadata
```

`config`, `result_metadata` and the `canonical_inputs` /
`produced_artifacts` key vocabulary are integration-specific and not
registered by the contract. Credentials and ArtifactStore selection
(`SCENEOPS_INTEGRATION_ARTIFACT__*`) cross the process boundary through
the runtime's environment, never as a field of either model.

## 2. Ownership

```text
SceneOps platform    job orchestration; Dataset / DatasetVersion / Scene / Episode state;
                     DB transactions; ArtifactRecord registration; lineage
Integration runtime  external SDK dependencies; external-format writing;
                     ArtifactStore reads/writes of the artifacts it needs
```

A runtime never opens a DB session, imports `sceneops-db`, runs in Celery,
writes an ArtifactRecord or mutates canonical state
(`packages/sceneops-analytics/tests/test_package_boundaries.py`).

## 3. LeRobot export

```text
SceneOpsDataset.open(...)                 a pinned learning-data export on MinIO
  -> IntegrationRequest (EXPORT, external_ref.format = "lerobot")
       built by scripts/e2e/lerobot_build_request.py
  -> LeRobot integration container        tools/lerobot-integration (own uv.lock, Dockerfile);
                                          entrypoint sceneops_analytics.external_adapters
                                          .lerobot.entrypoint
  -> LeRobot v3 dataset at external_ref.uri
  -> official LeRobotDataset reader, frame-by-frame comparison with the export   (make e2e-episode-learning)
```

No worker job invokes the LeRobot runtime; `make e2e-episode-learning` runs it
(request built in the worker image, export in the isolated container, read-back
in the same container image with the official reader). LeRobot stays in its own uv project and image for
dependency and runtime isolation: a minimal, reproducible closure that never
pulls in `sceneops-db`, Celery or the worker's dependencies.

## 4. Limitations

- Export only, and only LeRobot. A second format (e.g. RLDS) adds an
  adapter behind the same `ExternalDatasetAdapter` / `ExternalDatasetWriter`
  contract ([Dataset interoperability](./dataset-interoperability.md)).
- No worker-side executor or job type runs an integration runtime; no
  integration-run record is persisted.

## 5. Source-of-truth map

- Contract: `packages/sceneops-core/sceneops_core/integration_runtime/`, `packages/sceneops-core/tests/test_integration_runtime.py`
- LeRobot entrypoint: `packages/sceneops-analytics/sceneops_analytics/external_adapters/lerobot/entrypoint.py`
- Isolated LeRobot environment/container: `tools/lerobot-integration/`
- E2E: `scripts/e2e/e2e_lerobot_container_roundtrip.sh`, `scripts/e2e/smoke_lerobot_container.sh`
- Make targets: `makefiles/lerobot.mk` (`lerobot-sync`, `lerobot-test`, `lerobot-image`); the container is the `lerobot-integration` compose service (`compose/lerobot.yaml`), driven by `make e2e-episode-learning`
