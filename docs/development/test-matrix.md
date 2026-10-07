# Test matrix

What each test layer proves and where a given contract is verified. Point-in-time
results (counts, measured values) are recorded with the decision that produced
them, in [ADR-007](../adr/007-canonical-ingestion-architecture.md) §34, not here.
The command surface is in [local-development.md](./local-development.md).

The validation surface is deliberately small:

```text
make test                       unit
make test-integration           real PostgreSQL / MinIO, disposable database + bucket
make test-infrastructure        real infrastructure beyond those, SUITE = pipelines | recovery | kafka | boundaries
make reference-contract-verify  the golden contract (read-only)
make e2e-streaming-equivalence  Recording Import ≡ Streaming Acquisition (read-only)
make e2e-scene-ml               derived journey
make e2e-episode-learning       derived journey
make e2e-cleanroom              reconstruction (destructive)
```

Environment commands (`reference-data-bootstrap`, `reference-contract-bootstrap`,
`local-reset`) prepare or reset what these validate and are not tests. Operator diagnostics
(`check-*`, `disk-report`, …) and `benchmarks/` are not acceptance gates ([below](#operator-tools-and-benchmarks)).

Use the smallest layer that proves a behavior; use real infrastructure whenever
infrastructure semantics are part of the contract.

## Taxonomy

The terms the commands, scripts and documents share:

| Term | Meaning | Commands |
| --- | --- | --- |
| Source Preparation | nuScenes → the locked MCAP corpus under `data/reference`; touches no PostgreSQL, MinIO, Redis or Kafka | `reference-data-bootstrap` (`reference-data-verify` re-checks it) |
| Recording Import | an existing MCAP → a RobotRun | `canonical-bootstrap`, composed by the contract bootstrap |
| Streaming Acquisition | a locked MCAP replayed through ROS 2 → Kafka → capture → publish → a RobotRun | `streaming-bootstrap`, composed by the contract bootstrap |
| RobotRun boundary | both modes end at a registered RobotRun; Scenes, Episodes and everything derived start from it and do not depend on the mode | `e2e-streaming-equivalence` proves the two RobotRuns of a fixture are equivalent |
| Golden Reference Contract | the 20 RobotRuns (10 fixtures × both modes) with their Scenes and Episodes, under fixed identities, in the reference environment ([reference-contract.md](./reference-contract.md)) | `reference-contract-bootstrap`, `reference-contract-verify` |
| Derived test workspace | the fixed, test-owned Datasets `sceneops-test-scene-ml` and `sceneops-test-episode-learning` that hold the derived test state of the `REFERENCE_DERIVED` journeys; a repeated run reuses them | `e2e-scene-ml`, `e2e-episode-learning` |
| Disposable test runtime | a PostgreSQL database (`sceneops_test`) and MinIO bucket (`sceneops-test`) created for one run and dropped after it, plus, for `pipelines`, an execution runtime of its own (compose project `sceneops-test`); never the reference database, bucket, api or workers | `test-integration`, `test-infrastructure` (`pipelines`, `recovery`) |

`DISPOSABLE_RUNTIME=1` is a different thing: it declares that the *reference* runtime will be
reset afterwards, which a workflow that registers RobotRuns of its own requires.

## Layers

| Layer | Command | Infrastructure | Proves |
| --- | --- | --- | --- |
| Unit | `make test` | none | pure logic, schemas, state transitions, pipeline-definition and REF-chaining contracts, execution identity, API services over fakes, the orchestrator's state machine over fakes, the disposable-environment safety rules, the golden contract's evaluation and equivalence-pair resolution, the equivalence verifier's decisions |
| Isolated-environment unit | `make test-infrastructure SUITE=boundaries` (acquisition tool, LeRobot adapter), `SUITE=kafka` (ROS 2 bridge and capture) | the tool's own uv project / the ROS 2 image (`kafka` also needs the broker) | the acquisition tool (incl. its import boundary), the LeRobot adapter and container entrypoint, the streaming bridge and capture, including the lifecycle envelope of a run against a real broker |
| Integration | `make test-integration` | PostgreSQL, MinIO (a disposable database and bucket the command creates and drops) | repositories and the migrated schema (every model column exists; no column a model dropped remains), ArtifactStore semantics, MinIO selective Parquet reads, the registrars, acquisition reconciliation, artifact lifecycle classification, the recording Scene / Episode and derived verticals against real stores. A module is integration when it is named `*_integration.py` (or lives in `sceneops-db` / `sceneops-storage` tests) |
| Infrastructure acceptance | `make test-infrastructure` (`SUITE=pipelines`, the default) | PostgreSQL, MinIO (a disposable database and bucket) + a disposable execution runtime: API, Celery workers and Redis of its own on them | pipeline execution contracts: dedup / force, convergence, conflict-then-replacement, blocked resumption, failure recovery, concurrent registration, RobotRun registration idempotency, the orchestrator that ran them (PipelineRun -> PipelineTaskRun -> Job -> Celery -> JobRunner). `DISPOSABLE_ENVIRONMENT`: its DatasetVersions are the fixed `sceneops-test-infra-pipelines/<test>`, in the disposable database |
| Recovery acceptance | `make test-infrastructure SUITE=recovery` | PostgreSQL, MinIO (the same disposable database and bucket) + a throwaway Redis and Celery workers (Docker) | acquisition recovery under injected faults, one per test: finalized capture never published, a Job left queued, a worker killed during registration, a committed registration whose completion is lost, transient failures across replacement Jobs and the attempt budget, Redis down / unresponsive then restored, concurrent reconciliation passes, and that conflicts and integrity incidents are left untouched. And the full-lifecycle acceptance: nine acquisitions, each hit by a different failure between capture and RobotRun, recovered only by the production commands. And worker loss under the Job ownership lease: a worker killed after its claim, a pool child killed in its handler (refused immediate redelivery), a paused owner fenced after a redelivery of its own message took over, a handler blocking the event loop keeping its lease, duplicate messages running a Job once. And lost messages: the broker down at the API's dispatch, an accepted message lost by the broker, the orchestrator's dispatch, a lost `advance`, lease recovery's lost send, duplicate and racing resends, each pipeline completing with one Job per task |
| Smoke | `make test-infrastructure SUITE=kafka` (after the bridge and capture tests) | Kafka | transport and liveness only; never creates domain data |
| E2E journey | the four `make e2e-*` ([ADR-007](../adr/007-canonical-ingestion-architecture.md) §36) | live stack + containers | a user journey through production paths (below); the test-state class of each is in [Test-state classes](#test-state-classes) |
| Clean room | `make e2e-cleanroom` | an empty generated runtime (it resets one) + Kafka and ROS 2 | the golden contract is reconstructed from the preserved reference inputs alone and the primary journeys consume it without changing it (`CLEANROOM_ACCEPTANCE`) |
| Model-backend acceptance (opt-in) | `make acceptance-grounding-dino` | inference server | the Scene ML journey with the real detector |
| Baseline bootstraps (building blocks) | `canonical-bootstrap`, `streaming-bootstrap` and their read-only `*-verify` / `streaming-compare`: callable, not advertised | live stack (+ Kafka and ROS 2 images for the streamed one) | what `reference-contract-bootstrap` composes: create-or-verify of one scope's baseline, its registered per-channel counts against the lock, its canonical agreement with the other baseline. Validated through the contract, not on their own |
| Golden reference contract | `make reference-contract-verify`, `make reference-contract-bootstrap` (unit: `scripts/reference/tests` in `make test`) | live stack (+ Kafka and ROS 2 images for the bootstrap) | exactly the contract's 20 RobotRuns (10 fixtures × Recording Import and Streaming Acquisition) with their Scenes and Episodes: identity, provenance, locked message and channel facts, whole-recording shape; reports non-contract RobotRuns; the bootstrap reuses what is complete and never replaces an identity |
| Command surface | `make check-commands` | none | the advertised commands exist and nothing references removed architecture |
| Runtime source boundary | `make test-infrastructure SUITE=boundaries` (`make check-runtime-boundary` alone) | Docker (starts no platform service) | only the acquisition / reference-preparation services mount the raw dataset; every normal runtime service sees neither `/data/raw` nor `/input/nuscenes` and keeps the paths it needs |

## Operator tools and benchmarks

Neither is a test layer.

- **Operator tools** — the environment checks, liveness probes, API / worker inspection
  commands, reconciliation and status reports, and `disk-report` — are manual diagnostics
  ([local-development.md](./local-development.md#operator-tools)). They assert no SceneOps
  contract.
- **Benchmarks** are [`benchmarks/`](../../benchmarks/README.md): one workload measured on
  one machine, with no pass / fail assertion and no Make command. A benchmark result is
  recorded as point-in-time evidence and claims only what was measured.

## Test-state classes

Platform state is durable and nothing in the platform removes it, so every workflow is
classed by what it leaves behind. This is the one vocabulary used by the docs, the
scripts and `make help`. The local reference environment holds the
[golden reference contract](./reference-contract.md) and, once the derived workflows have
run, their fixed test-owned state; anything else is residue of a disposable runtime and
is dropped by `make local-reset`, never deleted piecemeal
([ADR-007](../adr/007-canonical-ingestion-architecture.md) §35.3).

| Class | May create | Runs on | Workflows |
| --- | --- | --- | --- |
| `REFERENCE_CONTRACT` | the contract's RobotRuns, Scenes and Episodes, under their fixed identities; a repeated run reuses them | the reference environment | `reference-contract-bootstrap`; `canonical-bootstrap` / `streaming-bootstrap` with the contract's identity (`smoke-1` selects `scene-0061` of it) |
| `REFERENCE_READ_ONLY` | nothing: no PostgreSQL row, no MinIO object, no Kafka record. `e2e-streaming-equivalence` fingerprints every RobotRun, Dataset, Scene and Episode before and after and fails on any difference | the reference environment | `reference-contract-verify`, `e2e-streaming-equivalence` (reads the contract's two RobotRuns of one fixture from the ArtifactStore), the transport smoke of `SUITE=kafka`, and the building blocks' `*-verify` / `streaming-compare` |
| `REFERENCE_DERIVED` | no RobotRun and nothing in a contract DatasetVersion; derived state only under a fixed, test-owned Dataset (`sceneops-test-*`) whose identity does not depend on time, a counter or a random value, so a repeated run reuses it | the reference environment | `e2e-scene-ml` (`sceneops-test-scene-ml`), `acceptance-grounding-dino` (`sceneops-test-scene-ml-grounding-dino`), `e2e-episode-learning` (`sceneops-test-episode-learning`) |
| `MUTATING_ACQUISITION` | RobotRuns, captures, Kafka records, their artifacts | a disposable runtime (`DISPOSABLE_RUNTIME=1`; refused otherwise) | any bootstrap with a non-contract `BASELINE_ID` |
| `CLEANROOM_ACCEPTANCE` | everything: it resets the runtime first, reconstructs the whole contract, and runs both L3 journeys into their fixed `sceneops-test-*` Datasets, so it ends in the state of a bootstrapped reference environment plus those two Datasets | a runtime it resets (`make local-reset`, confirmed) | `e2e-cleanroom` |
| `DISPOSABLE_ENVIRONMENT` | anything its tests write, including the execution history they generate | a PostgreSQL database (`sceneops_test`) and a MinIO bucket (`sceneops-test`) created for the run and dropped after it, on the local servers; never the reference database or bucket. the `pipelines` suite also runs its own execution runtime on them ([below](#the-disposable-execution-runtime)) | `make test-integration`, `make test-infrastructure` (`SUITE=pipelines`, `recovery`) |

### `REFERENCE_DERIVED` convergence

A derived workflow consumes the golden RobotRun, Scene and Episode and may add state of
its own. That state has a fixed identity and every record in it is addressed by what it
consumes, so running the workflow again changes nothing it already holds:

- the Dataset and DatasetVersion are reused (`baseline` for the journeys);
- Scenes and Episodes are reused by `canonical-bootstrap`'s create-or-verify;
- the LabelSet, sample views, aligned Episodes and learning export are content-pinned
  revisions that converge on retry;
- the ScenarioSet, InferenceRun and EvaluationRuns of Scene ML carry ids derived from the
  Dataset (`scset-<dataset>`, `infer-<dataset>`, `eval-<dataset>`, `eval-<dataset>-recheck`);
- where the platform replaces state (a changed Scene configuration), the test uses the
  platform's explicit replacement (`replace=True`), never deletion.

### Execution history

Execution history is not derived state and is append-only by platform design: every
forced Job or PipelineRun adds a row, and a re-executed validation, profile, mining or
readiness stage writes a report under the id of the Job that ran it. Production
semantics are not changed to make tests converge, and nothing removes a row.

```text
REFERENCE_DERIVED
→ a fixed semantic derived identity; a repeated run reuses it

execution history
→ append-only; a workflow whose subject is re-execution generates it on purpose
→ lives in disposable test infrastructure, never on the reference environment
```

The journeys keep the history they add to the reference environment bounded by not
re-executing what is not their subject: the Scene ML pipeline and the first Episode
learning pipeline are requested without `force`, so an identical request returns the run
that holds the result. The infrastructure suites exist to prove re-execution (force,
retry, resumption, concurrency, orchestration), so they run on a disposable execution
runtime and their history is dropped with it. Executing `evaluate_detection` again under an
existing evaluation run id registers duplicate ArtifactRecords for the same objects, so the
Scene ML journey runs its second evaluation once and reads it afterwards.

### The disposable environment

`make test-integration` and the `pipelines` and `recovery` suites of
`make test-infrastructure` run
through `tests/infrastructure/disposable_env.py`: it recreates `sceneops_test` (migrated with
the repository's alembic migrations) and `sceneops-test` from scratch, runs the suite
with `SCENEOPS_DATABASE_URL` and `MINIO_BUCKET` pointing at them, and drops both
afterwards, whether the suite passed, failed or was interrupted. A run killed before it
could drop leaves them behind; the next run recreates them, so nothing needs manual
cleanup. Neither command reads or writes the reference database or bucket: the runner
refuses names outside `sceneops_test*` / `sceneops-test*` or equal to the reference's
(`POSTGRES_DB`, `MINIO_BUCKET`), and the `require_disposable_environment` pytest plugin
aborts a session whose environment points anywhere else. The tests therefore delete
nothing to restore the environment; the recovery suites still use a Redis container and
Celery workers of their own, and the infrastructure suites the execution runtime below.

### The disposable execution runtime

The `pipelines` suite of `make test-infrastructure` runs through
`tests/infrastructure/disposable_env.py run --runtime`:

```text
create the database + bucket
→ start the compose project `sceneops-test` (compose/test-runtime.yaml)
→ run the suite against that runtime's API
→ stop the project (containers, volumes)
→ drop the database + bucket
```

The project runs the same `api` and `worker` images and the same settings as the
reference environment, with the database, the ArtifactStore root and the Celery broker
replaced: an API on a free loopback port, `sceneops.jobs` and `sceneops.pipeline_runs`
workers, and a Redis of its own. Only the PostgreSQL and MinIO *servers* are shared with
the reference environment, as for `test-integration`; its api, workers and Redis are
neither used nor reconfigured, and the reference database and bucket are not opened.

The suite needs one RobotRun of the golden contract. Nothing is copied from the reference
environment: the `baseline` fixture runs the production create-or-verify path
(`canonical_bootstrap.sh`, `smoke-1`) against the runtime's API, publishing the locked
recording into the disposable bucket, so it needs the prepared reference corpus
(`make reference-data-bootstrap`) and the images (`make local-up`) and nothing else.

Isolation is enforced rather than assumed. The runner refuses names outside
`sceneops_test*` / `sceneops-test*` or equal to the reference's, the compose file takes the
database and bucket only from variables the runner supplies (a missing one fails the
interpolation), the runner reads the settings the running API, worker and scheduler hold
and aborts if any names another database or bucket, and the `api` fixture
refuses to run against an API that a make target did not start. A run killed before it
could tear down leaves containers; the next start removes them first (the project name is
fixed), like the database and bucket.

### Reference checks and test-owned identities

Test-owned identities carry the reserved `test-` namespace: `run-test-*` and
`robot-test-*` for disposable RobotRuns, `sceneops-test-*` for Datasets. The reference
environment is checked in two strengths:

```text
make reference-contract-verify
→ the contract is valid: 20 RobotRuns, 20 Scenes, 20 Episodes
→ reports non-contract RobotRuns, derived test datasets (`sceneops-test-*`) and foreign
  datasets, each apart, without failing

make reference-contract-verify REQUIRE_PRISTINE=1
→ additionally requires zero of all three: a freshly reset and bootstrapped environment
```

The contract stays valid while the fixed derived datasets exist; only `REQUIRE_PRISTINE`
treats them as a violation.

## The journeys

| Journey | Source → result | Proves |
| --- | --- | --- |
| `e2e-streaming-equivalence` | the contract's Recording Import RobotRun and Streaming Acquisition RobotRun of one fixture (`smoke-1` → `scene-0061`), each read from the ArtifactStore: its registered recording and manifest, Scenes and Episodes | transport preservation: each RobotRun pins its own recording and carries the locked counts; acquisition equivalence (per-channel payloads and counts, source times, `/tf_static`); canonical Scene / Episode equivalence (I-35); negative controls (a dropped message, a 1 ns source-time shift, a changed payload checksum, a Scene and an Episode semantic change); nothing in the platform changed. It replays, captures, publishes and registers nothing and needs no Kafka or ROS 2 |
| `e2e-scene-ml` | reference RobotRun → Scenes (`sceneops-test-scene-ml`) → LabelSet → sample views → ScenarioSet → prediction → evaluation | every derived revision pins what it consumed; retries converge; the real lidar payload decodes; canonical Scenes are untouched; a second run adds no Dataset, Scene, artifact or MinIO object |
| `e2e-episode-learning` | reference RobotRun → Episodes (`sceneops-test-episode-learning`) → AlignedEpisodes → learning export → verification + LeRobot round trip | pinned, deterministic alignment and export; shard checksums and `SceneOpsDataset` reads; every LeRobot frame equals the export's dense window; canonical Episodes are untouched; a second run adds no Dataset, Scene, Episode, artifact or MinIO object |
| `e2e-cleanroom` | preserved inputs → reset → empty runtime → `reference-data-verify` → `reference-contract-bootstrap` → `reference-contract-verify REQUIRE_PRISTINE=1` → second bootstrap → `e2e-scene-ml` + `e2e-episode-learning` (`scene-0061`, each twice) → `reference-contract-verify` | reconstruction and nothing else: the generated stores are empty and the inputs byte-identical after the reset; the contract is rebuilt to exactly 20 RobotRuns, 20 Scenes and 20 Episodes with no other RobotRun or Dataset; a second bootstrap imports, replays, builds and writes nothing and starts no Kafka, ROS 2 or replay container; the journeys add no RobotRun, leave the contract's records unchanged and add exactly the two fixed Datasets, and a second run of each adds no Dataset, Scene, Episode, artifact or MinIO object. It repeats no transport, recovery, infrastructure, orchestrator, model-backend or benchmark check |

## Where a contract is verified

| Contract | Layer |
| --- | --- |
| Pipeline task chains, REF hand-offs, no defaulted source vocabulary | unit (`apps/worker/tests/pipelines/`) |
| A stage takes its pinned input from the upstream REF; explicit params win | unit (`test_pipeline_contracts.py`) |
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
| Execution through the durable Job path: the orchestrator submits Jobs, `JobRunner` runs them and reports back | infrastructure (`test_pipeline_execution.py`) |
| Reconstruction of the golden runtime from the preserved inputs, and the convergence of a repeated bootstrap | E2E (`e2e-cleanroom`) |
| Recording Import golden contract: the RobotRun pins the locked recording and its message / channel facts, one whole-recording Scene and Episode per run, validated, profiled and ready | `make reference-contract-verify` / `canonical-verify`; evaluator over synthetic state in `scripts/reference/tests` |
| Whole-recording Scene / Episode shape: declared source clocks, `[running marker, completed marker + 1)`, source-faithful asynchronous streams, no outcome / annotation field | unit over a synthetic ROS 2 recording (`apps/worker/tests/{scenes,episodes}/test_recording_*_builder.py`, `packages/sceneops-core/tests/test_{scene_manifest,episodes}.py`) |
| Scene / Episode independence and payload sharing: one payload set per RobotRun, reused by the Episode build, ids independent of build configuration | unit (`test_scene_and_episode_builders_are_independent_siblings`); integration (`test_recording_episode_vertical_integration.py`, `test_recording_scene_vertical_integration.py`) |
| Disposable test environment: only `sceneops_test*` / `sceneops-test*` accepted, the reference names refused by the runner and by the pytest plugin, `test-integration` and the `recovery` suite wired through the runner | unit (`tests/infrastructure/unit/test_disposable_env.py`); create, migrate, drop and rerun after an interruption are exercised by the two commands themselves |
| Disposable execution runtime: every service of `compose/test-runtime.yaml` takes its database, ArtifactStore root and broker from the runner (own Redis, loopback ports, no reference service name), a publisher aimed at the reference bucket is refused, the `pipelines` suite wired through the runner, `SUITE=` dispatch; the running processes hold the disposable settings | unit (`tests/infrastructure/unit/test_execution_runtime.py`); the running API and workers are read back by the runner on every start (`verify_isolation`) |
| Four-pipeline surface and task chains | unit (`apps/worker/tests/pipelines/test_pipeline_definitions.py`) |
| MinIO selective Parquet reads | integration (`packages/sceneops-analytics/tests/test_selective_reads_minio_integration.py`) |
| Recording Import / Streaming Acquisition equivalence of the registered RobotRuns | E2E (`e2e-streaming-equivalence`, read-only); the verifier's identity, timing, canonical and negative-control decisions in `scripts/e2e/tests` |
| Equivalence pair resolution (both golden identities and the lock's facts from the contract) and the platform fingerprint | unit (`scripts/reference/tests`) |
| Lifecycle envelope of a streamed run: one `RUN_START`, the run's telemetry records, one `RUN_END`, in order and in their own sequence spaces; capture finalized by the explicit `RUN_END`; the receipt's offsets spanning `message_count + 2` records | real Kafka (`ros2/capture/tests/test_lifecycle_integration.py` via `SUITE=kafka` / `make ros2-test`: the real bridge node publishes, capture consumes); the bridge's emission over a fake producer in `ros2/nodes/tests/test_streaming_bridge_node.py` |
| Lineage pins, LeRobot round trip | E2E (`e2e-scene-ml`, `e2e-episode-learning`) |
| Derived test state has a fixed identity and converges: no time / counter / random Dataset id, a repeated journey adds nothing it already holds | unit (`scripts/e2e/tests/test_derived_identities.py`, static); E2E (each journey run twice, state snapshots compared) |
| The golden contract stays valid beside derived test state; `REQUIRE_PRISTINE` additionally requires none | unit (`scripts/reference/tests`); `make reference-contract-verify` |
| One test-state vocabulary; retired class names and flags appear nowhere in active docs, scripts or Make | `make check-commands` |

## Rules

- A smoke target never creates persistent application-domain data.
- A real-infrastructure command (`test-integration` and the `pipelines` and `recovery`
  suites of `test-infrastructure`) fails when any test is skipped,
  so an unreachable PostgreSQL, MinIO, API or Docker is never a green run. A
  module that belongs to another suite is excluded from the others, not skipped. The
  `kafka` and `boundaries` suites run their tools' own pytest sessions and checks and
  report their own failures; the real-nuScenes test of the acquisition tool skips without
  `data/raw/nuscenes`.
- A workflow that registers RobotRuns of its own never runs on the reference
  environment: it requires `DISPOSABLE_RUNTIME=1`, and the runtime is reset afterwards.
- A `REFERENCE_DERIVED` workflow names its Dataset with a fixed identity (no timestamp,
  counter or UUID) and never writes into a contract DatasetVersion. It runs again
  without adding Datasets, DatasetVersions, Scenes, Episodes or derived manifests.
- No test removes platform state through SQL or a test-only API to restore an
  environment; disposable state is dropped with the runtime, or, for `test-integration`
  and the `pipelines` and `recovery` suites, with their disposable database
  and bucket (and execution runtime).
- `test-integration` and the `pipelines` and `recovery` suites never touch the
  reference database or bucket; `pipelines` also uses none of the reference
  environment's api, workers or Redis.
- A workflow whose subject is re-execution (force, retry, resumption, concurrency,
  orchestration) generates execution history on purpose and therefore runs on a
  disposable execution runtime, never on the reference environment.
- An infrastructure behavior (offsets, locks, object semantics, DDS, image
  contents) is claimed only from a layer that runs the real infrastructure.
- An infrastructure check is not a user journey: retry, dedup, force,
  replacement and orchestrator behavior live in `tests/infrastructure`, not in
  the E2E scripts.
- A diagnostic (`check-*`, `disk-report`) or a benchmark is never cited as acceptance: a
  green diagnostic proves a machine is usable, a benchmark number proves nothing about
  correctness.
- A regression test reproduces the failure mechanism, not only the final value.
- "All tests pass" is never claimed from a subset: report each suite's command
  and counts.
