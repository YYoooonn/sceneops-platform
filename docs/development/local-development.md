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

`inference-server` and `airflow` are **not** part of the default stack —
they're genuinely optional (a separate detection backend and an alternate
pipeline-execution backend, respectively). Start them explicitly:
`make inference-local-up` / `make airflow-up`.

## Stopping vs. resetting

- `make local-down` — stops and removes the containers, **keeps** the
  Postgres/Redis/MinIO volumes. Your data survives; `make local-up` picks
  up where you left off.
- `make local-reset` — **destructive**. Deletes the Postgres/Redis/MinIO
  volumes and everything under `./data/{datasets,runs,models,artifacts}`,
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
- `.env.airflow.local` / `.env.airflow.example` are deliberately separate —
  Airflow runs its own, entirely independent Postgres instance with its own
  credentials, not SceneOps'.
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
`airflow`/`ros2`/`gpu`/`debug` — it's required infrastructure (the artifact
store backend), not an optional extra, so `api`/`worker-*` can
`depends_on: minio: condition: service_healthy` directly, and a plain
`docker compose down -v` actually reaches `minio-data`.

## Testing

See [test-matrix.md](./test-matrix.md) for what each test layer proves and the
layer a given contract belongs to; this section is the command surface.

```
make test                          unit suites, no infrastructure (run anywhere)
make test-integration              real Postgres + MinIO, needs `make local-up`
make test-infrastructure           pipeline contracts on the live stack, needs `make local-up`
make test-infrastructure-airflow   the same pipelines through Airflow (opt-in, see below)
make test-recovery                 acquisition recovery under injected faults + the full-lifecycle acceptance (Docker, needs `make local-up`)
make e2e-batch-canonical | e2e-streaming-equivalence | e2e-scene-ml | e2e-episode-learning
make e2e-cleanroom                 the full-platform acceptance (DESTRUCTIVE: runs `make local-reset`)
make check-commands                the command surface is consistent (no pytest, no stack)
```

- `make test` runs each unit suite in its own pytest process (`apps/worker`,
  `apps/api`, `apps/inference-server`, `packages/sceneops-{core,analytics,
  integrations,streaming}`): several suites ship their own `tests/__init__.py`
  and conftest, which pytest cannot register together. Every `inference-server`
  test mocks `GroundingDinoModel`/`ImageResolver` — none needs a GPU, model
  weights or a running inference server.
- `make test-integration` covers `packages/sceneops-db/tests` (real Postgres,
  including a check that the migrated schema has no column the models dropped),
  `packages/sceneops-storage/tests` (real MinIO), and the worker's registrar and
  recording Scene / Episode vertical tests against both. No pytest marker selects
  them: they live in files that `make test`'s suites never touch. Each
  `sceneops-db`/`sceneops-storage` test either rolls back or deletes what it
  wrote, so they are safe on the persistent local stack.
- `make test-infrastructure` runs `tests/infrastructure` against the live stack:
  the four-pipeline surface, execution-key dedup / force, convergence of an
  unchanged rebuild, conflict-then-explicit-replacement, resumption of a blocked
  pipeline, recovery of a failed one, concurrent runs over one scope, the
  orchestrator that executed them, and MinIO selective Parquet reads. It builds on
  the canonical baseline (`canonical-bootstrap`, create-or-verify) and writes only
  into throwaway DatasetVersions, so the baseline's scope is never mutated.
  `make test-infrastructure-airflow` runs the same canonical pipelines through the
  Airflow per-task DAGs; it needs `make airflow-up` and the `api` service
  restarted with `SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow` (a
  process-startup setting).
- `make test-recovery` runs the two acquisition-recovery suites against the live
  PostgreSQL and MinIO with a Redis container and Celery workers of its own, so
  killing a worker or stopping the broker never touches the dev stack. Each test
  uses its own MinIO RobotRun root and `rec124-` rows, removed afterwards. The
  production commands run as subprocesses (`publish-pending`, `reconcile --once
  --apply`, `acquisition_status`); only `recovery_worker` and `recovery_publisher`
  add a fault point. It needs no canonical baseline. The suites share one harness
  (`tests/infrastructure/recovery_support.py`).
- On Apple Silicon hosts, running `sceneops-db`'s async engine outside Docker
  requires `greenlet`, which `sqlalchemy`'s own platform-marker-gated extra
  silently excludes there — `packages/sceneops-db` depends on it directly.

## E2E journeys

Exactly five, each a user journey through production paths. Platform operations
go through FastAPI; bulk data moves through one-shot containers and the
ArtifactStore. The host needs Docker Compose, curl and jq — no uv, no PostgreSQL
or MinIO access, no worker CLI.

```
make e2e-batch-canonical [SCENE=...]         dataset fixture -> MCAP -> RobotRun (Recording Import) -> Scenes -> Episodes
make e2e-streaming-equivalence [SCENE=...]   the locked reference MCAP via the Recording Import baseline and via replay -> ROS2 -> Kafka -> capture: equivalent (needs Kafka)
make e2e-scene-ml [SCENE=...]                Scenes -> labels -> sample views -> ScenarioSet -> prediction -> evaluation (mock backend)
make e2e-episode-learning [SCENE=...]        Episodes -> AlignedEpisodes -> learning export -> verification + LeRobot round trip
make e2e-cleanroom                           fresh state -> canonical-bootstrap -> both L3 journeys -> final verification
```

There is no bare `make e2e` aggregate: the journeys differ in what they need
(default stack / ROS 2 + Kafka / the LeRobot image), and an aggregate would hide
which one a failure needed. `make e2e-cleanroom` is the only full-platform
acceptance entry point.

- `e2e-batch-canonical` acquires a recording, registers the RobotRun, runs
  `canonical-bootstrap` (Scene and Episode building over that one RobotRun) and
  checks the canonical records against the recording they came from: windows on
  the declared clocks, source-faithful streams, pinned manifest revisions, shared
  payloads, validated and profiled units, and that re-running the bootstrap
  changes no record.
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
- **`make e2e-cleanroom`**: `make local-reset` (destructive — fresh
  Postgres/Redis/MinIO, preserves `data/raw`), images from the current tree,
  `canonical-bootstrap`, `canonical-verify`, `e2e-scene-ml` and
  `e2e-episode-learning` on that baseline, and a final verification through the
  API. It needs no GPU, Airflow or Kafka. Requires confirmation unless `FORCE=1`.

`make smoke-api` and `make smoke-streaming` are liveness / transport checks, not
journeys: they never create persistent domain data.

### Canonical baseline

`make canonical-bootstrap` builds the reproducible L1/L2 baseline of a reference
corpus scope (prepared recordings -> one RobotRun per fixture -> Scenes ->
Episodes, validated and profiled; run `make reference-data-bootstrap` first) and
`make canonical-verify` re-checks it read-only; see [canonical-baseline.md](./canonical-baseline.md). The Scene and
Episode build configurations every journey uses are the files under
`config/baselines/`. `make streaming-bootstrap` builds the same baseline by replaying
each fixture's locked recording through ROS 2 -> Kafka -> capture (needs Kafka and
`make reference-data-bootstrap`), `make streaming-verify` re-checks it read-only and
`make streaming-compare` compares it with the Recording Import baseline.

### Identity

`DATASET_ID`/`DATASET_VERSION` mean **SceneOps' own canonical identity only**,
never constrained by an external dataset's naming. A baseline is named by
`BASELINE_ID`: the RobotRun is `run-<BASELINE_ID>-<fixture>`, the robot
`robot-<BASELINE_ID>`, the DatasetVersion `sceneops-<BASELINE_ID>/baseline`. The
persistent baselines are `ref-smoke-1` and `ref-nuscenes-mini-full-10`, and their
streamed counterparts `stream-ref-smoke-1` and `stream-ref-nuscenes-mini-full-10` (see
[canonical-baseline.md](./canonical-baseline.md)); a journey that mutates its scope (or that you
simply run again) uses a unique id per run (`e2e-<journey>-<timestamp>-<pid>`),
and `BASELINE_ID=ref-smoke-1 make e2e-scene-ml` runs it on the persistent baseline.
Every pipeline-run-creating script passes `force: true`, so re-running a journey
re-executes its pipelines instead of returning an old run through execution-key
dedup, and assertions are scoped to values the current run returned, not to
global counts.

### Disk usage

Each baseline stores its recording and extracted payloads in MinIO (the nuScenes
mini `scene-0061` recording is ~350 MB), and the Kafka and MinIO volumes keep
growing across runs. There is no automated cleanup: `make local-reset` is the
supported way to clear generated PostgreSQL / Redis / MinIO state (it keeps
`data/raw`), and `make clean-artifacts` clears generated `./data` output. Journeys
remove the scratch they own — the MCAP in the acquisition volume after
publication, the label document, the LeRobot export directory.
