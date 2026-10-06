# Test matrix

What each test layer proves and where a given contract is verified. Point-in-time
results (counts, measured values) are recorded with the decision that produced
them, in [ADR-007](../adr/007-canonical-ingestion-architecture.md) §34, not here.
The command surface is in [local-development.md](./local-development.md).

Use the smallest layer that proves a behavior; use real infrastructure whenever
infrastructure semantics are part of the contract.

## Layers

| Layer | Command | Infrastructure | Proves |
| --- | --- | --- | --- |
| Unit | `make test` | none | pure logic, schemas, state transitions, pipeline-definition and REF-chaining contracts, execution identity, API services over fakes, the Airflow DAG mirror |
| Isolated-environment unit | `make acquisition-test`, `make lerobot-test`, `make ros2-test` | the tool's own uv project / the ROS 2 image (`ros2-test` also Kafka) | the acquisition tool (incl. its import boundary), the LeRobot adapter and container entrypoint, the streaming bridge and capture |
| Integration | `make test-integration` | PostgreSQL, MinIO | repositories and the migrated schema (every model column exists; no column a model dropped remains), ArtifactStore semantics, the registrars, acquisition reconciliation, artifact lifecycle classification, the recording Scene / Episode verticals against real stores |
| Infrastructure acceptance | `make test-infrastructure` | the live stack (+ the canonical baseline) | pipeline execution contracts: the four-pipeline surface, dedup / force, convergence, conflict-then-replacement, blocked resumption, failure recovery, concurrent registration, RobotRun registration idempotency, the orchestrator that ran them, MinIO selective Parquet reads |
| Recovery acceptance | `make test-recovery` | PostgreSQL, MinIO + a throwaway Redis and Celery workers (Docker) | acquisition recovery under injected faults, one per test: finalized capture never published, a Job left queued, a worker killed during registration, a committed registration whose completion is lost, transient failures across replacement Jobs and the attempt budget, Redis down / unresponsive then restored, concurrent reconciliation passes, and that conflicts and integrity incidents are left untouched. And the full-lifecycle acceptance: nine acquisitions, each hit by a different failure between capture and RobotRun, recovered only by the production commands |
| Orchestrator acceptance | `make test-infrastructure-airflow` | live stack + Airflow | the canonical pipelines through the Airflow per-task DAGs |
| Smoke | `make smoke-api`, `make smoke-streaming` | the API / Kafka | transport and liveness only; never creates domain data |
| E2E journey | the five `make e2e-*` | live stack + containers | a user journey through production paths (below) |
| Clean room | `make e2e-cleanroom` | fresh platform state | the platform reconstructs its baseline and runs its representative workflows from nothing |
| Model-backend acceptance | `make acceptance-grounding-dino` | inference server | the Scene ML journey with the real detector |
| Streaming baseline | `make streaming-bootstrap`, `streaming-verify`, `streaming-compare` | live stack + Kafka + ROS 2 images | the persistent streamed baseline of a scope (create-or-verify, resumable), its registered per-channel counts against the lock, and its canonical agreement with the Recording Import baseline; not a journey and not a benchmark |
| Golden reference contract | `make reference-contract-verify`, `make reference-contract-bootstrap` (unit: `scripts/reference/tests` in `make test`) | live stack (+ Kafka and ROS 2 images for the bootstrap) | exactly the contract's 20 RobotRuns (10 fixtures × Recording Import and Streaming Acquisition) with their Scenes and Episodes: identity, provenance, locked message and channel facts, whole-recording shape; reports non-contract RobotRuns; the bootstrap reuses what is complete and never replaces an identity |
| Command surface | `make check-commands` | none | the advertised commands exist and nothing references removed architecture |
| Runtime source boundary | `make check-runtime-boundary` | Docker (starts no platform service) | only the acquisition / reference-preparation services mount the raw dataset; every normal runtime service sees neither `/data/raw` nor `/input/nuscenes` and keeps the paths it needs |

## The journeys

| Journey | Source → result | Proves |
| --- | --- | --- |
| `e2e-batch-canonical` | dataset fixture → MCAP → RobotRun (Recording Import) → Scenes + Episodes | L1 → L2 canonicalization from one recording: windows on declared clocks, source-faithful streams, pinned manifest revisions, shared payloads, validated and profiled units, and that re-running the bootstrap changes no record |
| `e2e-streaming-equivalence` | locked reference MCAP → Recording Import baseline RobotRun, and → replay → ROS 2 → Kafka → capture → publish-pending → reconcile → streamed RobotRun; both → Scenes + Episodes | transport preservation: acquisition equivalence (per-channel payloads, source times, `/tf_static`), the run's Kafka control records, canonical Scene / Episode equivalence (I-35), negative controls, baseline untouched |
| `e2e-scene-ml` | Scenes → LabelSet → sample views → ScenarioSet → prediction → evaluation | every derived revision pins what it consumed; retries converge; the real lidar payload decodes; canonical Scenes are untouched |
| `e2e-episode-learning` | Episodes → AlignedEpisodes → learning export → verification + LeRobot round trip | pinned, deterministic alignment and export; shard checksums and `SceneOpsDataset` reads; every LeRobot frame equals the export's dense window; canonical Episodes are untouched |
| `e2e-cleanroom` | fresh state → canonical-bootstrap → both L3 journeys | supported production paths only: no direct SQL, no direct MinIO inspection, no host worker CLI |

## Where a contract is verified

| Contract | Layer |
| --- | --- |
| Pipeline task chains, REF hand-offs, no defaulted source vocabulary | unit (`apps/worker/tests/pipelines/`) |
| A stage takes its pinned input from the upstream REF; explicit params win | unit (`test_pipeline_contracts.py`) |
| Airflow DAG mirrors the definitions | unit (`test_airflow_dag_mirror.py`) |
| Alignment of several pinned Episodes; execution-key identity of `align_episode` | unit (`test_align_episode_handler.py`, `apps/api/tests/platform/test_job_service_align_episode.py`) |
| SceneManifest v2 has no annotation structure; v1 bytes are refused | unit (`packages/sceneops-core/tests/test_scene_manifest.py`) |
| DatasetVersion carries no channel requirements or metadata | unit + integration (`test_dataset_version_*`, `test_migration_schema.py`) |
| Canonical Scene / Episode registration, replacement, fail-loud | integration (`apps/worker/tests/{scenes,episodes}/*integration.py`) |
| Acquisition reconciliation: every lifecycle state classified from real MinIO + PostgreSQL facts, unchanged state and identical report on a second run | integration (`apps/worker/tests/robots/test_reconciliation_vertical_integration.py`); classification rules over doubles in `apps/api/tests/robots/test_reconciliation.py` |
| Artifact lifecycle of `robot_runs/`: the reference query by URI, pending rules, O1 / O2 candidates and their grace periods, dangling and corrupt references, conservative handling of malformed publication, deterministic report for a fixed observation time, nothing written | unit over doubles (`apps/api/tests/robots/test_artifact_lifecycle.py`); `list_by_uri_prefix` / `list_run_ids_for_root` on real PostgreSQL (`packages/sceneops-db/tests/test_reconciliation_read_repositories.py`); every class built through the real publisher, registrar and capture scan on real MinIO + PostgreSQL, with the one-shot command compared to the in-process report (`apps/worker/tests/robots/test_artifact_lifecycle_vertical_integration.py`) |
| Acquisition recovery: bounded action per state, abandon-then-replace, the attempt budget across replacement Jobs, dispatch failure, the per-pass bound | unit over doubles (`apps/api/tests/robots/test_reconciliation_recovery.py`); `abandon_if_inactive` single winner on real PostgreSQL (`packages/sceneops-db/tests/test_job_abandon.py`); real faults in `tests/infrastructure/test_acquisition_recovery.py` |
| `publish-pending`: converges on retry, resumes after the recording upload, never overwrites a conflict, one failing capture does not block the rest | unit (`packages/sceneops-integrations/tests/test_publish_pending.py`); real MinIO in `test_acquisition_recovery.py` |
| Acquisition status and operational report: the stage, health and operator-required table for every state, derived failure / attempt / timing / size facts, artifact findings raising health, the aggregates (by stage / health, registration and budget use, oldest work, incidents, bytes), determinism for a fixed observation time, nothing written or stored | unit over doubles (`apps/api/tests/robots/test_acquisition_status.py`); the one-shot command against real MinIO + PostgreSQL in the full-lifecycle acceptance |
| Structured recovery records: one line per action with state before / after, retry-budget evidence and outcome, a pass line, stdout kept the JSON report, a failed second observation never failing a pass | unit (`apps/api/tests/robots/test_recovery_logging.py`, `packages/sceneops-integrations/tests/test_publish_pending.py`); parsed from the commands' stderr in the full-lifecycle acceptance |
| The whole lifecycle under failure: finalized capture without operator metadata, publisher killed between recording and manifest, a failed publish, a broker outage, concurrent recovery, a killed worker, a spent attempt budget, a forged manifest and a deleted recording left untouched, one RobotRun per acquisition | infrastructure (`tests/infrastructure/test_acquisition_lifecycle_acceptance.py`); fault points in `recovery_worker.py` and `recovery_publisher.py` |
| `ArtifactStore.list_objects` (recursive, paginated, atomic-write temp files excluded) | unit (`test_local_artifact_store.py`) + integration (`test_s3_artifact_store.py`) |
| Retry, dedup, force, replacement, blocked / failed resumption, concurrency | infrastructure (`tests/infrastructure/test_pipeline_execution.py`) |
| Celery / Airflow execution | infrastructure (`test_pipeline_execution.py`, `test_airflow_backend.py`) |
| Recording Import / Streaming Acquisition equivalence | E2E (`e2e-streaming-equivalence`) |
| Lineage pins, LeRobot round trip | E2E (`e2e-scene-ml`, `e2e-episode-learning`) |

## Rules

- A smoke target never creates persistent application-domain data.
- An infrastructure behavior (offsets, locks, object semantics, DDS, image
  contents) is claimed only from a layer that runs the real infrastructure.
- An infrastructure check is not a user journey: retry, dedup, force,
  replacement and orchestrator behavior live in `tests/infrastructure`, not in
  the E2E scripts.
- A regression test reproduces the failure mechanism, not only the final value.
- "All tests pass" is never claimed from a subset: report each suite's command
  and counts.
