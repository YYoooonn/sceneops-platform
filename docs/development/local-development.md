# Local development

The canonical way to run SceneOps locally. `make help` is the quick
reference; this doc explains the *why* behind it. See
[../architecture/overview.md](../architecture/overview.md) for what the
platform actually does once it's running.

## Bootstrap

```bash
cp .env.example .env.local   # once, then edit if you need non-default ports/creds
make setup                   # install deps + pre-commit hooks
make local-up                # idempotent: infra -> health -> MinIO buckets -> migrate -> API + workers
```

`make local-up` runs, in order:

1. `postgres`, `redis`, `minio` — started and waited on until healthy (Compose
   `up -d --wait`, not just "container started").
2. `minio-init` — idempotent bucket bootstrap (`mc mb --ignore-existing`).
   Copies no data into the bucket. Safe to re-run.
3. `db-migrate` — `alembic upgrade head`. Safe to re-run (Alembic no-ops at
   head).
4. `api`, `worker-pipeline`, `worker-jobs` — started once their
   dependencies (Postgres/Redis/MinIO all healthy) are satisfied.

Every step is idempotent, so `make local-up` is safe to run repeatedly —
against an already-running stack it just confirms everything is healthy and
re-applies nothing destructive.

`inference-server` is **not** part of the default stack — it is a genuinely
optional detection backend. Start it explicitly: `make inference-local-up`
(CPU) or `make inference-gpu-up`.

## Stopping vs. resetting

- `make local-down` — stops and removes the containers, **keeps** the
  Postgres/Redis/MinIO volumes. Your data survives; `make local-up` picks
  up where you left off.
- `make local-reset` — **destructive**. Deletes the Postgres/Redis/MinIO
  volumes and everything under `./data/{runs,artifacts}`,
  then runs `make local-up` again against a clean state. This does **not**
  rebuild any Docker image — it reuses whatever `api`/`worker`/integration
  images are already built (`docker compose up`, not `--build`). If you've
  changed application source since the images were last built, run
  `make compose-build` first, or the reset will bring up a clean database
  against stale code. Asks for interactive confirmation unless `FORCE=1`.
  This is the *only* destructive local-state target.
- `make db-reset` — narrower: wipes only the Postgres volume (not
  Redis/MinIO), then re-migrates. Useful when you just want a clean schema.
- `make compose-build` — rebuilds the `api`/`worker-pipeline` images
  (`worker-pipeline`/`worker-jobs`/`worker-cli` share one image, see
  `x-worker-common` in `compose/workers.yaml`). Neither `local-up` nor
  `local-reset` does this for you — application code is baked into these
  images at build time (`COPY apps/worker ./apps/worker`), not
  live-mounted, so a source change requires an explicit `compose-build`
  before it takes effect in a running stack.

## Configuration: env files and precedence

```
shell environment                              (highest precedence)
  -> .env.local        gitignored, your real local config
  -> .env.example       committed defaults (documents every supported var)
  -> application settings class defaults        (lowest precedence)
```

- `.env.example` is committed and documents every variable SceneOps reads
  locally, with safe, harmless defaults (`minioadmin`/`sceneops`/etc. — not
  real secrets). `cp .env.example .env.local` to get started.
- `.env.local` is gitignored. Docker Compose loads it two ways that must
  both point at the same file to stay consistent:
  - `--env-file .env.local` on every `docker compose` invocation (wired up
    once via `$(COMPOSE)` in the root `Makefile`) — this is what lets
    `${POSTGRES_PORT:-5432}`-style values in `compose/core.yaml`
    actually pick up your overrides, not just their hardcoded defaults.
  - `env_file: [.env.local]` on each service — injects the same values into
    the container's own runtime environment.
  - The API and worker's own settings classes (`apps/api/app/config.py`,
    `apps/worker/sceneops_worker/config.py`) also load `.env.local` directly
    (for host-side/non-Docker runs); real environment variables always win
    over anything loaded from a file.
- `.env.test` (if present under `apps/*/tests`) governs test-time config
  only; it has no bearing on `make local-up`.

Override a port without editing any file: `POSTGRES_PORT=5433 make local-up`.

## Service topology vs. configuration

`compose.yaml` (via `include:` of `compose/*.yaml`) should describe
*topology* (which services exist, what they depend on, health checks) and
provide only harmless local fallbacks (`${POSTGRES_DB:-sceneops}`) — not be
the primary place environment-specific values live. Real values live in
`.env.local`.

The Compose project is named `sceneops` (set via `name:` in `compose.yaml`)
and `compose.yaml` at the repo root is the canonical entrypoint, so plain
`docker compose ...` (no `-f`) resolves it via normal discovery from the
repo root — that's what `$(COMPOSE)` in the Makefile and the scripts under
`scripts/` rely on.

`minio` is intentionally **not** profile-gated, unlike `inference`/
`ros2`/`gpu`/`debug` — it's required infrastructure (the artifact
store backend), not an optional extra, so `api`/`worker-*` can
`depends_on: minio: condition: service_healthy` directly, and a plain
`docker compose down -v` actually reaches `minio-data`.

## Testing

See [test-matrix.md](./test-matrix.md) for what each test layer proves and the
layer a given contract belongs to; this section is the command surface.

```
make test                          unit suites, no infrastructure (run anywhere)
make test-integration              real Postgres + MinIO in a disposable database + bucket, needs `make local-up`
make test-infrastructure [SUITE=pipelines|recovery|kafka|boundaries]
                                   real-infrastructure suites; `pipelines` is the default (see below)
make reference-contract-verify     the golden contract is valid (read-only)
make e2e-streaming-equivalence | e2e-scene-ml | e2e-episode-learning
make e2e-cleanroom                 the acceptance of reconstruction (DESTRUCTIVE: runs `make local-reset`)
make check-commands                the command surface is consistent (no pytest, no stack)
```

- `make test` runs each unit suite in its own pytest process (`apps/worker`,
  `apps/api`, `apps/inference-server`, `packages/sceneops-{core,analytics,
  integrations,streaming}`): several suites ship their own test-package `__init__.py`
  and conftest, which pytest cannot register together. Every `inference-server`
  test mocks `GroundingDinoModel`/`ImageResolver` — none needs a GPU, model
  weights or a running inference server.
- `make test-integration` covers `packages/sceneops-db/tests` (real Postgres,
  including a check that the migrated schema has no column the models dropped),
  `packages/sceneops-storage/tests` (real MinIO), and every module named
  `*_integration.py` under `apps/worker/tests`, `packages/sceneops-analytics/tests`
  and `apps/api/tests` (registrars, reconciliation, recording Scene / Episode and
  derived verticals, selective Parquet reads, the API request transaction) against both. A new `*_integration.py` is picked up without
  editing the Makefile; `make test` collects the same files but they skip without
  `SCENEOPS_DATABASE_URL` / `MINIO_ENDPOINT_URL`. The real-infrastructure commands run
  with `-p require_infrastructure` (`tests/infrastructure/require_infrastructure.py`):
  a skipped test, such as an unreachable Postgres or MinIO, fails the command. The
  suites run in a disposable database (`sceneops_test`, migrated with alembic) and a
  disposable bucket (`sceneops-test`) that `tests/infrastructure/disposable_env.py`
  creates before the run and drops after it, so the reference database and bucket are
  never written to and no test cleans up after itself. A run killed before the drop
  leaves them behind and the next run recreates them;
  `uv run python tests/infrastructure/disposable_env.py status` shows whether they
  exist, and `... drop` removes them. `TEST_POSTGRES_DB` / `TEST_MINIO_BUCKET` rename
  them; names outside `sceneops_test*` / `sceneops-test*`, or equal to `POSTGRES_DB` /
  `MINIO_BUCKET`, are refused.
- `make test-infrastructure SUITE=<name>` selects one real-infrastructure suite (default
  `pipelines`). `pipelines` and `recovery` fail, never skip, when their infrastructure
  is missing, and the selection changes no suite's isolation:
  - `pipelines` runs `tests/infrastructure` on a disposable execution runtime:
    execution-key dedup / force, convergence of an unchanged rebuild,
    conflict-then-explicit-replacement, resumption of a blocked pipeline, recovery
    of a failed one, concurrent runs over one scope and the orchestrator that
    executed them. These tests re-execute pipelines on purpose and the platform keeps every
    Job and PipelineRun that results, so the command creates the disposable database and
    bucket of `make test-integration`, starts an API, Celery workers and a Redis of its own
    on them (compose project `sceneops-test`, `compose/test-runtime.yaml`), seeds the one
    RobotRun of the golden contract it consumes by the production create-or-verify path
    (`canonical-bootstrap`'s script, `smoke-1`, from the prepared reference recording), and
    removes everything afterwards. The reference api, workers, Redis, database and bucket
    are not used or modified; the tests build into the fixed Dataset
    `sceneops-test-infra-pipelines`, one DatasetVersion per test, inside the disposable
    database. It needs `make local-up` (the PostgreSQL / MinIO servers and the images) and
    `make reference-data-bootstrap`, and takes its ports from the free ones on the host.
  - `recovery` runs the two acquisition-recovery suites against PostgreSQL and MinIO in the
    same disposable database and bucket as `make test-integration`, with a Redis container
    and Celery workers of its own, so killing a worker or stopping the broker never touches
    the dev stack. Each test uses its own MinIO RobotRun root and `rec124-` rows; the
    database and bucket are dropped as a whole. The production commands run as subprocesses
    (`publish-pending`, `reconcile --once --apply`, `acquisition_status`); only
    `recovery_worker` and `recovery_publisher` add a fault point. It needs no canonical
    baseline. The suites share one harness (`tests/infrastructure/recovery_support.py`);
    `RECOVERY_TESTS=<path>` runs one module.
  - `kafka` runs the ROS 2 bridge and capture tests in the `ros2` image (real-Kafka
    integration included) and then the transport smoke, which publishes a deterministic
    sequence and verifies envelope recovery, per-RobotRun ordering and partitioning. It needs
    `make streaming-up` and creates no domain data.
  - `boundaries` runs the isolation boundaries: the dataset-acquisition tool's tests,
    including its import boundary, and the LeRobot adapter's tests, each in its own uv
    project; the I-36 check of the acquisition and replay images; and the check that only the
    acquisition / reference-preparation services mount the raw dataset.
  `uv run python tests/infrastructure/disposable_env.py status` shows leftovers of a killed
  run; `docker compose -p sceneops-test down -v` removes a leftover runtime.
- On Apple Silicon hosts, running `sceneops-db`'s async engine outside Docker
  requires `greenlet`, which `sqlalchemy`'s own platform-marker-gated extra
  silently excludes there — `packages/sceneops-db` depends on it directly.

## E2E journeys

Four journeys, each a user journey through production paths. Platform operations
go through FastAPI; bulk data moves through one-shot containers and the
ArtifactStore. The host needs Docker Compose, curl and jq — no uv, no PostgreSQL
or MinIO access, no worker CLI.

```
make e2e-streaming-equivalence [SCENE=...]   the contract's Recording Import and Streaming Acquisition RobotRuns of one fixture, read-only: equivalent (needs neither Kafka nor ROS 2)
make e2e-scene-ml [SCENE=...]                Scenes -> labels -> sample views -> ScenarioSet -> prediction -> evaluation (mock backend)
make e2e-episode-learning [SCENE=...]        Episodes -> AlignedEpisodes -> learning export -> verification + LeRobot round trip
make e2e-cleanroom                           reset -> golden contract from the preserved inputs -> both L3 journeys -> contract unchanged
```

There is no bare `make e2e` aggregate: the journeys differ in what they need
(default stack / ROS 2 + Kafka / the LeRobot image), and an aggregate would hide
which one a failure needed. `make e2e-cleanroom` is the acceptance of
reconstruction.

- `e2e-scene-ml` and `e2e-episode-learning` start from a baseline built by
  `canonical-bootstrap`. They derive everything else (labels, sample views,
  ScenarioSets, predictions, evaluations, aligned episodes, exports) and prove
  every revision pins what it consumed and that the canonical Scenes / Episodes
  are untouched. `e2e-episode-learning` also needs the LeRobot image
  (`make lerobot-image`, built by the target). The label document reaches the
  worker through `data/inputs/labels/` (the worker's input root; gitignored,
  removed after the run). It is rendered from the fixture's locked reference labels;
  the journey reads no source dataset.
- `acceptance-grounding-dino` runs the Scene ML journey with the GroundingDINO
  backend (needs `make inference-local-up` or `inference-gpu-up`): the model-backend
  acceptance, separate from the default mock-backend journey.
- **`make e2e-cleanroom`**: images from the current tree, then `make local-reset`
  (destructive — PostgreSQL, Redis, MinIO, the Kafka log, the capture volume and
  the generated `./data` artifacts; preserves `data/raw`, `data/reference` and
  `config/reference`) and a read-back proof that the runtime is empty;
  `reference-data-verify`; `reference-contract-bootstrap` and
  `reference-contract-verify REQUIRE_PRISTINE=1` (20 RobotRuns, 20 Scenes, 20
  Episodes, nothing else); a second `reference-contract-bootstrap` that must
  converge without importing, replaying, starting Kafka, building or changing a
  record or object; `e2e-scene-ml` and `e2e-episode-learning` on `scene-0061`,
  each run twice; and a final `reference-contract-verify` that shows the contract
  valid and unchanged beside the two fixed derived Datasets. It ends with timing
  and storage diagnostics (not a benchmark). It needs ROS 2 and Kafka (the
  contract's Streaming Acquisition RobotRuns) but no GPU, and runs
  none of the other acceptance surfaces. Requires confirmation unless `FORCE=1`.

The transport smoke (`SUITE=kafka`) is a liveness / transport check, not a journey: it never
creates persistent domain data.

### Reference baselines

`make reference-contract-bootstrap` builds the reference environment: the two baselines of
the [golden reference contract](./reference-contract.md), composed from the baseline
bootstraps (`canonical-bootstrap` for Recording Import, `streaming-bootstrap` for Streaming
Acquisition; run `make reference-data-bootstrap` first), and `make reference-contract-verify`
re-checks it read-only. The baseline bootstraps and their read-only verifiers
(`canonical-verify`, `streaming-verify`, `streaming-compare`) stay callable for debugging one
fixture or one mode; they are building blocks, not part of the validation surface. See
[canonical-baseline.md](./canonical-baseline.md). The Scene and Episode build configurations
every journey uses are the files under `config/baselines/`.

### Identity

`DATASET_ID`/`DATASET_VERSION` mean **SceneOps' own canonical identity only**,
never constrained by an external dataset's naming. A baseline is named by
`BASELINE_ID`: the RobotRun is `run-<BASELINE_ID>-<fixture>`, the robot
`robot-<BASELINE_ID>`, the DatasetVersion `sceneops-<BASELINE_ID>/baseline`. The
persistent baselines are `ref-nuscenes-mini-full-10` and its streamed counterpart
`stream-ref-nuscenes-mini-full-10` (the [golden reference contract](./reference-contract.md);
`REFERENCE_SCOPE=smoke-1` selects `scene-0061` of them and creates no baseline of its own; see
[canonical-baseline.md](./canonical-baseline.md)). The L3 journeys use the reference RobotRun
and write into a fixed, test-owned Dataset (`sceneops-test-scene-ml`,
`sceneops-test-episode-learning`) that every run reuses; `e2e-streaming-equivalence`
reads the contract's two RobotRuns and creates no state at all; a workflow that registers
RobotRuns of its own uses a unique `test-` id per run and only runs on a disposable runtime
(`DISPOSABLE_RUNTIME=1`, [test-matrix.md](./test-matrix.md#test-state-classes)).
Pipeline-run-creating scripts pass `force: true` by default, so re-running one
re-executes its pipelines instead of returning an old run through execution-key
dedup, and assertions are scoped to values the current run returned, not to
global counts. The exceptions are the Scene ML pipeline and the first Episode learning
pipeline of the journeys: they are requested without `force`, because a forced
re-execution of the Scene ML stages appends scenario-mining and readiness reports under
fresh Job ids on every run (`test-matrix.md`, Execution history).

## Operator tools

Diagnostics and manual operations are **operator tools, not acceptance gates**: they answer
"is my machine / stack in a usable state" or perform one operation by hand, assert no
SceneOps contract, and a green result proves nothing about correctness. Correctness is the
[validation surface](./test-matrix.md).

| Command | What it tells you |
| --- | --- |
| `make check-env` | `.env.local`, `uv.lock`, `uv` and `docker compose` exist |
| `make check-imports` | the built `api` and `worker` images import their packages |
| `make check-celery` | Redis answers and both Celery workers reply to `inspect ping` |
| `make check-runtime-boundary` | no normal runtime service mounts the raw dataset (also part of `SUITE=boundaries`) |
| `make check-inference-server` / `check-inference-server-ready` | the inference server is alive / has loaded its model |
| `make ros2-check` | `rclpy` and the MCAP storage plugin exist in the `ros2` image |
| `make api-health` / `api-openapi` / `show-runs` / `show-pipeline` / `show-job-events` | the API answers; a run, pipeline or job as the API reports it |
| `make worker-run-job JOB_ID=…` / `worker-advance-pipeline PIPELINE_RUN_ID=…` | run one Job through `JobRunner`, or take one `PipelineOrchestrator` step, by hand through the worker CLI (`sceneops-worker jobs run`, `sceneops-worker pipelines advance`) |
| `make reconcile-once` / `reconcile-apply` / `artifact-lifecycle-once` / `acquisition-status` | one acquisition-reconciliation pass; read-only lifecycle and status reports ([ADR-008](../adr/008-acquisition-lifecycle-reliability.md)) |
| `make disk-report` | disk headroom and what could be reclaimed (below) |

`make check-commands` is different in kind: it is static, needs no stack, and is part of how
the command surface itself is kept consistent.

## Benchmarks

The scripts under [`benchmarks/`](../../benchmarks/README.md) measure one workload on one
machine and assert nothing. No make target runs one and none is acceptance; their results
are recorded as point-in-time evidence next to the decision they informed.

## Disk hygiene

Platform state is durable and nothing in the platform removes it, so the local runtime only
grows: each baseline stores its recording and extracted payloads in MinIO (the nuScenes mini
`scene-0061` recording alone is ~350 MB), and the Kafka log keeps every streamed run until
the broker's retention expires it. There is no automated cleanup and no selective removal of
platform state. `make disk-report` is read-only and shows the host headroom (the streaming
bootstrap stops below `MIN_FREE_GIB`, default 6, on the host and the Docker VM), the
generated directories, the SceneOps volumes, the Kafka log, disposable-test leftovers and
what Docker could reclaim. Reclaim in this order, stopping when there is room:

| Target | Safe? | How |
| --- | --- | --- |
| Disposable test resources (`sceneops_test`, `sceneops-test`, compose project `sceneops-test`) | always; a run removes its own, a killed run leaves them | `uv run python tests/infrastructure/disposable_env.py drop`, `docker compose -p sceneops-test down -v` |
| Stopped containers, dangling images | yes | `docker container prune`, `docker image prune` |
| Docker build cache | yes; the next image build is slower | `docker builder prune` (`--filter until=168h` keeps the recent layers) |
| Generated `./data` output, Python caches | yes; `data/raw` and `data/reference` are untouched | `make clean-artifacts`, `make clean-python` |
| `cache/hf` (model weights) | yes if no inference server is used; re-downloaded on next use | `rm -rf cache/hf/*` |
| Unused images (`docker image prune -a`) | **costly, not unsafe**: it also removes the opt-in `ros2`, `dataset-replay`, `dataset-acquisition`, and `lerobot-integration` images, which the journeys and the contract bootstrap rebuild (GBs of build time) | only when the room is needed |
| Kafka log | not selectively. The telemetry topic reports `retention.ms` = 7 days and 1 GiB segments (the repository sets neither): the broker deletes rolled segments once their newest record is that old, so the log drains by itself after the last streamed run. Every streamed run owns its offset range and a capture can only resume from what the topic still holds, so truncating by hand is not supported | wait, or `make local-reset` |
| Acquisition scratch (`sceneops_acquisition-recordings`) | transient MCAPs are removed once their recording is provably registered; a capture that is not provably published is kept on purpose and is recovered with `publish-pending` / `reconcile`, not deleted | `make acquisition-status` |

Never remove with Docker or the shell: `data/raw`, `data/reference`, `config/reference`
(the preserved reference inputs), and the `sceneops_minio-data` and `sceneops_postgres-data`
volumes (the golden state). **Do not run `docker volume prune` or `docker system prune
--volumes`**: after `make local-down` every SceneOps volume is unreferenced, including those
two. `make local-reset` is the one supported way to clear generated runtime state
(PostgreSQL, Redis, MinIO, the Kafka log and the capture volume); afterwards
`make reference-contract-bootstrap` rebuilds the reference environment from the locked
corpus. Journeys remove the scratch they own: the MCAP in the acquisition volume after
publication, the label document, the LeRobot export directory.
