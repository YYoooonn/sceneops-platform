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
| Integration | `make test-integration` | PostgreSQL, MinIO | repositories and the migrated schema (every model column exists; no column a model dropped remains), ArtifactStore semantics, MinIO selective Parquet reads, the registrars, acquisition reconciliation, artifact lifecycle classification, the recording Scene / Episode and derived verticals against real stores. A module is integration when it is named `*_integration.py` (or lives in `sceneops-db` / `sceneops-storage` tests) |
| Infrastructure acceptance | `make test-infrastructure` | the live stack (+ the canonical baseline) | pipeline execution contracts: dedup / force, convergence, conflict-then-replacement, blocked resumption, failure recovery, concurrent registration, RobotRun registration idempotency, the orchestrator that ran them |
| Recovery acceptance | `make test-recovery` | PostgreSQL, MinIO + a throwaway Redis and Celery workers (Docker) | acquisition recovery under injected faults, one per test: finalized capture never published, a Job left queued, a worker killed during registration, a committed registration whose completion is lost, transient failures across replacement Jobs and the attempt budget, Redis down / unresponsive then restored, concurrent reconciliation passes, and that conflicts and integrity incidents are left untouched. And the full-lifecycle acceptance: nine acquisitions, each hit by a different failure between capture and RobotRun, recovered only by the production commands |
| Orchestrator acceptance | `make test-infrastructure-airflow` | live stack + Airflow | the canonical pipelines, including Scene ML over a test-owned DatasetVersion and LabelSet, through the Airflow per-task DAGs |
| Smoke | `make smoke-streaming` | Kafka | transport and liveness only; never creates domain data |
| E2E journey | the four `make e2e-*` ([ADR-007](../adr/007-canonical-ingestion-architecture.md) §36) | live stack + containers | a user journey through production paths (below); the test-state class of each is in [Test-state classes](#test-state-classes) |
| Clean room | `make e2e-cleanroom` | fresh platform state | the platform reconstructs its baseline and runs its representative workflows from nothing |
| Model-backend acceptance | `make acceptance-grounding-dino` | inference server | the Scene ML journey with the real detector |
| Streaming baseline | `make streaming-bootstrap`, `streaming-verify`, `streaming-compare` | live stack + Kafka + ROS 2 images | the persistent streamed baseline of a scope (create-or-verify, resumable), its registered per-channel counts against the lock, and its canonical agreement with the Recording Import baseline; not a journey and not a benchmark |
| Golden reference contract | `make reference-contract-verify`, `make reference-contract-bootstrap` (unit: `scripts/reference/tests` in `make test`) | live stack (+ Kafka and ROS 2 images for the bootstrap) | exactly the contract's 20 RobotRuns (10 fixtures × Recording Import and Streaming Acquisition) with their Scenes and Episodes: identity, provenance, locked message and channel facts, whole-recording shape; reports non-contract RobotRuns; the bootstrap reuses what is complete and never replaces an identity |
| Command surface | `make check-commands` | none | the advertised commands exist and nothing references removed architecture |
| Runtime source boundary | `make check-runtime-boundary` | Docker (starts no platform service) | only the acquisition / reference-preparation services mount the raw dataset; every normal runtime service sees neither `/data/raw` nor `/input/nuscenes` and keeps the paths it needs |

## Test-state classes

Platform state is durable and nothing in the platform removes it, so every workflow is
classed by what it leaves behind. The local reference environment holds exactly the
[golden reference contract](./reference-contract.md); anything else is residue of a
disposable runtime and is dropped by `make local-reset`, never deleted piecemeal
([ADR-007](../adr/007-canonical-ingestion-architecture.md) §35.3).

| Class | May create | Runs on | Workflows |
| --- | --- | --- | --- |
| reference state | the contract's RobotRuns, Scenes and Episodes | the reference environment | `reference-contract-bootstrap`; `canonical-bootstrap` / `streaming-bootstrap` with the contract's identity (`smoke-1` selects `scene-0061` of it) |
| `READ_ONLY_REFERENCE` | no RobotRun; derived state only in a `sceneops-test-*` DatasetVersion of its own; never the reference DatasetVersion | the reference environment | `*-verify`, `streaming-compare`, `reference-contract-verify`, `smoke-streaming` (write nothing); `e2e-scene-ml`, `e2e-episode-learning`, `test-infrastructure` (Scenes / Episodes / derived records in `sceneops-test-*`) |
| `MUTATING_ACQUISITION_TEST` | RobotRuns, captures, Kafka records, their artifacts | a disposable runtime (`DISPOSABLE_RUNTIME=1`; refused otherwise) | `e2e-streaming-equivalence`, any bootstrap with a non-contract `BASELINE_ID` |
| self-cleaning fixtures | rows keyed by a per-test unique id | any runtime with PostgreSQL / MinIO | `make test-integration`, `make test-recovery` (their own teardown removes their rows) |

Temporary identities carry the reserved `test-` namespace: `run-test-*`,
`robot-test-*`, `sceneops-test-*`. The reference environment is checked with
`make reference-contract-verify REQUIRE_CLEAN=1` (20 contract RobotRuns, 0 non-contract
RobotRuns, 20 Scenes, 20 Episodes, 0 non-contract Datasets); after a `READ_ONLY_REFERENCE`
run it reports the `sceneops-test-*` DatasetVersions as residue while the contract
itself stays verified.

## The journeys

| Journey | Source → result | Proves |
| --- | --- | --- |
| `e2e-streaming-equivalence` | locked reference MCAP → the contract's Recording Import RobotRun (read as it is), and → replay → ROS 2 → Kafka → capture → publish-pending → reconcile → streamed RobotRun; both → Scenes + Episodes | transport preservation: acquisition equivalence (per-channel payloads, source times, `/tf_static`), the run's Kafka control records, canonical Scene / Episode equivalence (I-35), negative controls, baseline untouched |
| `e2e-scene-ml` | reference RobotRun → Scenes (own DatasetVersion) → LabelSet → sample views → ScenarioSet → prediction → evaluation | every derived revision pins what it consumed; retries converge; the real lidar payload decodes; canonical Scenes are untouched |
| `e2e-episode-learning` | reference RobotRun → Episodes (own DatasetVersion) → AlignedEpisodes → learning export → verification + LeRobot round trip | pinned, deterministic alignment and export; shard checksums and `SceneOpsDataset` reads; every LeRobot frame equals the export's dense window; canonical Episodes are untouched |
| `e2e-cleanroom` | fresh state → canonical-bootstrap (contract identity, `smoke-1`) → both L3 journeys | supported production paths only: no direct SQL, no direct MinIO inspection, no host worker CLI |

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
| Retry, dedup, force, replacement, blocked / failed resumption, concurrency, re-running a bootstrap or a registration changes no record | infrastructure (`tests/infrastructure/test_pipeline_execution.py`); the contract's snapshot diff; `created_payload_count == 0` on a repeated build in the recording verticals |
| Celery / Airflow execution | infrastructure (`test_pipeline_execution.py`, `test_airflow_backend.py`) |
| Recording Import golden contract: the RobotRun pins the locked recording and its message / channel facts, one whole-recording Scene and Episode per run, validated, profiled and ready | `make reference-contract-verify` / `canonical-verify`; evaluator over synthetic state in `scripts/reference/tests` |
| Whole-recording Scene / Episode shape: declared source clocks, `[running marker, completed marker + 1)`, source-faithful asynchronous streams, no outcome / annotation field | unit over a synthetic ROS 2 recording (`apps/worker/tests/{scenes,episodes}/test_recording_*_builder.py`, `packages/sceneops-core/tests/test_{scene_manifest,episodes}.py`) |
| Scene / Episode independence and payload sharing: one payload set per RobotRun, reused by the Episode build, ids independent of build configuration | unit (`test_scene_and_episode_builders_are_independent_siblings`); integration (`test_recording_episode_vertical_integration.py`, `test_recording_scene_vertical_integration.py`) |
| Four-pipeline surface and task chains | unit (`apps/worker/tests/pipelines/test_pipeline_definitions.py`) |
| MinIO selective Parquet reads | integration (`packages/sceneops-analytics/tests/test_selective_reads_minio_integration.py`) |
| Recording Import / Streaming Acquisition equivalence | E2E (`e2e-streaming-equivalence`) |
| Lineage pins, LeRobot round trip | E2E (`e2e-scene-ml`, `e2e-episode-learning`) |

## Rules

- A smoke target never creates persistent application-domain data.
- A real-infrastructure command (`test-integration`, `test-infrastructure`,
  `test-infrastructure-airflow`, `test-recovery`) fails when any test is skipped,
  so an unreachable PostgreSQL, MinIO, API or Docker is never a green run. An
  opt-in module with its own target is excluded from the others, not skipped.
- A workflow that registers RobotRuns of its own never runs on the reference
  environment: it requires `DISPOSABLE_RUNTIME=1`, and the runtime is reset afterwards.
- No test removes platform state through SQL or a test-only API; disposable state is
  dropped with the runtime.
- An infrastructure behavior (offsets, locks, object semantics, DDS, image
  contents) is claimed only from a layer that runs the real infrastructure.
- An infrastructure check is not a user journey: retry, dedup, force,
  replacement and orchestrator behavior live in `tests/infrastructure`, not in
  the E2E scripts.
- A regression test reproduces the failure mechanism, not only the final value.
- "All tests pass" is never claimed from a subset: report each suite's command
  and counts.
