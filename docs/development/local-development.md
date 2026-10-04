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
2. `minio-init` — idempotent bucket bootstrap (`mc mb --ignore-existing`,
   then mirrors `./data/raw` into the bucket). Safe to re-run.
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

See [test-matrix.md](./test-matrix.md) for actual, per-capability verification
status (unit/API/DB/job-pipeline/E2E/optional-runtime/clean-room/restart) —
this section covers only the command surface.

```
make test              infrastructure-independent, run anywhere, no prerequisites
make test-integration  real Postgres + MinIO, requires `make local-up` first
make e2e-cleanroom     the full-platform acceptance workflow, requires `make local-up` first
                        (DESTRUCTIVE -- runs `make local-reset` itself; see "E2E scope" below)
```

- `make test` covers `apps/worker`, `apps/api`, `apps/inference-server`,
  `packages/sceneops-core`, `packages/sceneops-analytics`, and
  `scripts/e2e/tests/test_e2e_fixture_bootstrap.py` (the E2E fixture
  bootstrap's own unit suite, Postgres faked in-memory). Every
  `inference-server` test mocks `GroundingDinoModel`/`ImageResolver` — none
  of it needs GPU, model weights, or a running inference server.
- `make test-integration` covers `packages/sceneops-db/tests` (real
  Postgres), `packages/sceneops-storage/tests` (real MinIO),
  `scripts/e2e/tests/test_e2e_fixture_bootstrap_integration.py` (the
  persistent E2E fixture bootstrap, see below), and
  `scripts/e2e/tests/test_pipeline_contracts_integration.py` (pipeline-
  definitions registry + unsupported-pipeline-type rejection -- lives here
  since it needs no nuScenes data at all), plus the worker's real
  PostgreSQL + MinIO registration and recording-Scene vertical tests
  (`apps/worker/tests/scenes/test_*_integration.py`). No pytest marker is
  used to select these — they live in dedicated test files/directories
  that `make test`'s testpaths never touch, which is sufficient selection
  on its own. Each `sceneops-db`/`sceneops-storage` test gets a fresh
  session/engine and either rolls back (transactional isolation — nothing
  is ever committed) or deletes what it wrote (`delete_prefix` under a
  dedicated `_test-integration/` key prefix) — safe to run against the
  same persistent local stack you're developing against.
  `test_e2e_fixture_bootstrap_integration.py` is the one deliberate
  exception: it commits, and does not clean up after itself —
  bootstrapping the shared `test-e2e-*` fixtures *is* the intended
  persistent effect, not test pollution (see "Persistent fixture
  bootstrap" below).
- On Apple Silicon hosts, running `sceneops-db`'s async engine outside
  Docker requires `greenlet`, which `sqlalchemy`'s own platform-marker-gated
  extra silently excludes there (`aarch64` is listed, macOS's `arm64` isn't)
  — `packages/sceneops-db` now depends on it directly, unconditionally.

## E2E scope

There is no bare `make e2e` aggregate. Scene building, robot learning
(ROS2 sandbox) and interoperability (isolated LeRobot venv) have materially different
infrastructure requirements, so bundling them under one aggregate would
hide which of those a failure actually needed. The primary surface is:

```
make e2e-recording-scene [SCENE=...]         nuScenes -> acquisition -> RobotRun -> canonical Scenes -> quality
make e2e-robot-learning [SCENE=... | MAX_SCENES=N]   real CAN bus -> ROS2 -> MCAP -> Episode -> learning export/curation
make e2e-interop                             real Postgres/MinIO -> SceneOpsDataset -> LeRobot -> golden comparison
make e2e-cleanroom                           THE full-platform acceptance workflow (see below)
```

`e2e-robot-learning` records its own CAN replay (needs the ROS2 sandbox,
`--profile ros2`, built on demand) and builds/curates the resulting
Episode(s) in one command — it is the primary path, not
`e2e-robot-can-replay`/`e2e-episode-building`/`e2e-episode-curation` run by
hand (those remain available as debug/stage targets, see `make help`'s
"Debug / Stage" section). See
[../workflows/robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md).

`e2e-interop` needs the isolated LeRobot environment (`make lerobot-sync`,
one-time) — real Postgres/MinIO round-trip (`interop` fixture ->
`SceneOpsDataset` -> LeRobot export -> official reader -> golden
comparison), see
[Dataset interoperability](../architecture/dataset-interoperability.md) §6.

**`make e2e-cleanroom`** is the only full-platform acceptance entry point:
`make local-reset` (destructive — wipes Postgres/Redis/MinIO, preserves
`data/raw/nuscenes` and the CAN bus expansion) -> `e2e-recording-scene` ->
`e2e-robot-learning` -> a final query of real persisted API state. It
deliberately does not require GPU, Airflow, or the isolated LeRobot venv —
those remain optional verification, run separately afterward
(`make verify-airflow-backend`, `make e2e-interop`).

**Unavailable until ADR-007 implementation step 10** (label ingress and a
derived synchronized-sample view): `make e2e-perception` and
`make e2e-scenario-curation` need ground truth and keyframes, which
recording-derived Scenes do not carry. **Until step 11**:
`make canonical-bootstrap` / `make canonical-verify` (the v0.0 baseline was
built by a removed pipeline). Each exits with an explicit message (script
exit code 3) instead of failing on removed infrastructure.

**Engineering-level checks live outside the `e2e-*` namespace** since they
aren't domain workflows from a real source to a persisted result:
- `make smoke-api` — transport/liveness only; creates no persistent
  Dataset/DatasetVersion/Model/PipelineRun (a smoke-* target must never
  leave behind domain data).
- `make smoke-lerobot-container` — container/transport-boundary check,
  isolated from the full pipeline.
- `make verify-reliability` — execution-key dedup/force + partial retry of
  a `recording_scene_building` run blocked at `validate_scene`, an
  execution-model property.
- `make verify-airflow-backend` — an alternate-orchestrator COMPATIBILITY
  CHECK: `recording_scene_building` through the Airflow per-task DAG PoC
  (not general backend substitution); needs `make airflow-up` + the `api`
  service restarted with `SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow`.

Both verification commands, and `e2e-scene-analytics-export`, take their
input from a registered camera RobotRun (`ensure_camera_robot_run` in
`scripts/e2e/lib.sh`): acquired once through the acquisition and publisher
containers under a deterministic run id, then reused.

`e2e-scene-analytics-export` is a secondary E2E, not part of
`e2e-cleanroom`'s core path.

All pipeline-run-creating scripts pass `force: true`, so re-running any of
these against an already-populated persistent stack genuinely re-executes
every pipeline rather than silently returning an old run via execution-key
dedup. `verify-reliability` is the one deliberate exception — dedup/force
*is* what it's testing, so its pipeline-run creation intentionally omits
`force` in the parts that assert dedup/resume behavior. Assertions
throughout are scoped to values returned by the current run
(`pipeline_run_id`, `run_id`s, etc.), not global counts, so they hold up
under a persistent stack's accumulated history — see
`scripts/e2e/e2e_episode_building.sh` or `scripts/e2e/verify_reliability.sh`
for the clearest examples.

### Canonical identity vs. external source/export identity

`DATASET_ID`/`DATASET_VERSION` mean **SceneOps' own canonical identity
only** — never constrained by an external dataset's own version naming, so
`DATASET_VERSION` is free to be `test-v1` everywhere. The nuScenes version
(`SOURCE_FORMAT_VERSION`) only selects what the acquisition tool reads; it
never reaches canonical identity. The LeRobot export target is described by
`ExternalDatasetRef` (`sceneops_core.integration_runtime`; see
[Dataset interoperability](../architecture/dataset-interoperability.md)).

### The E2E fixture catalog: `sceneops-e2e-v1`

Two shared logical fixtures cover every E2E workflow, rather than one
derived dataset identity per workflow, resolved via `scripts/e2e/lib.sh`'s
`resolve_e2e_fixture <name>`:

- **`core`** — `DATASET_ID=test-e2e-core`, `DATASET_VERSION=test-v1`,
  external source `SOURCE_FORMAT=nuscenes`/`SOURCE_FORMAT_VERSION=v1.0-mini`/
  `SOURCE_ROOT_URI=/data/raw/nuscenes`. Used by `e2e-scene-analytics-export`,
  `verify-reliability`, `verify-airflow-backend`, `e2e-robot-learning`,
  `e2e-episode-building`, and `e2e-episode-curation`. Scene and Episode families coexist on one
  DatasetVersion by design — each owns an independent summary sub-object
  that never overwrites the other's (see
  `packages/sceneops-core/tests/test_dataset_version_summaries.py`);
  `episode-building` additionally needs the MCAP fixture from
  `e2e-robot-can-replay` (see above), unrelated to this canonical identity.
- **`interop`** — `DATASET_ID=test-e2e-interop`, `DATASET_VERSION=test-v1`.
  Source: the deterministic golden learning fixture built by
  `sceneops_analytics.testing.interop_dataset`, for external-adapter/
  interoperability round-trip tests. Built directly by
  `make e2e-bootstrap-interop` (Python-only — no shell E2E *ingestion*
  path exists for it, unlike `core`); consumed on the export side by
  `make e2e-interop`'s real LeRobot round-trip (see
  [Dataset interoperability](../architecture/dataset-interoperability.md)
  §6/§7).


`smoke-api` sits outside the catalog entirely, and outside every other
fixture identity too — it creates zero persistent domain data: list
endpoints tolerant of an empty result, plus a 404/validation-error check on
a request guaranteed never to exist or persist. A smoke-* target must never
leave behind application-domain records, so no seeded "dev-smoke" dataset
was introduced either — read-only
list/404/validation checks already prove the same transport contract with
zero persistent fixture state, which is the smaller, more consistent
surface (see `scripts/e2e/smoke_api.sh`'s own header).

**Overriding**: every workflow still accepts an explicit identity, applied
exactly as given, never coerced into the `test-e2e-*` form —
`resolve_e2e_fixture` only fills in whichever of `DATASET_ID`/
`DATASET_VERSION`/`SOURCE_FORMAT_VERSION`/etc. the caller's environment left
unset (bash's `: "${VAR:=default}"` idiom):

```bash
make e2e-scenario-curation                                          # DATASET_ID=test-e2e-core DATASET_VERSION=test-v1
DATASET_ID=my-local-dataset DATASET_VERSION=v3 make e2e-scenario-curation   # runs against your own dataset instead
```

This is also why cleanup here is deliberately simple: **there is no
automated cleanup today**. `make local-up`'s design is purely
additive/idempotent (see above), and every pipeline-run-creating E2E script
already tolerates a persistent stack's accumulated history (see the
`force: true` paragraph above) rather than needing a clean slate. The one
rule that matters if/when automated cleanup is ever added: it may only ever
target the known `test-e2e-*`/`test-v1` identities, never a caller-supplied
one — this doc and `scripts/e2e/lib.sh`'s `resolve_e2e_fixture` are the
source of truth for what counts as "known test identity."

### Persistent fixture bootstrap

The catalog above describes *identity*; it says nothing about whether that
identity's data actually exists yet in a given local stack. One reusable,
idempotent bootstrap materializes it for real, so future E2Es (shell or
Python) can depend on known fixture state existing without depending on
another E2E having run first, with a hardened create/reuse/verify contract
(see "Package boundary" below).

**Seed boundary** — bootstrap only ever creates *prerequisite* state, never
the output whose production is the behavior some E2E actually tests:

- **`core`** — ensures the canonical DatasetVersion row exists. Nothing
  else: producing Scenes/Episodes is the building workflows' own job, not
  bootstrap's. Verification additionally checks
  the external nuScenes source directory is present on disk.
- **`interop`** — the *full* golden `LearningDataExportManifest` + its
  three Parquet tables + their `ArtifactRecord`s, persisted into real
  Postgres/MinIO. Unlike core, nothing currently or plannedly
  under test *produces* this snapshot — future external-adapter/round-trip
  tests only ever read from it — so materializing it completely is itself
  prerequisite state. The golden data is never redefined here; it comes
  from `sceneops_analytics.testing.interop_dataset`'s
  `build_interop_entries()`/`compute_expected_interop_episodes()`.
  Interop's bootstrap/verification never touches the nuScenes source path
  — that's a core-only check.

**Package boundary** — `sceneops-analytics`
is a production package (the columnar analytics export layer); it must not
depend on `sceneops-db` just to support E2E setup. So the orchestration
that actually talks to Postgres/MinIO — `scripts/e2e/e2e_fixture_bootstrap.py`
— lives outside that package, as a plain (non-installed) sibling module
next to the CLI that calls it (`scripts/e2e/bootstrap_e2e_fixtures.py`).
Only the deterministic, DB-free golden-data definitions
(`sceneops_analytics.testing.interop_dataset`) are still imported from
`sceneops-analytics`; a regression test
(`packages/sceneops-analytics/tests/test_package_boundaries.py`) enforces
that `sceneops-analytics` never re-acquires a `sceneops-db` import.

**API** — `scripts/e2e/e2e_fixture_bootstrap.py`:

```python
results = await bootstrap_e2e_fixtures(
    "interop",  # or "core" / "all"
    session=session, artifact_store=artifact_store, analytics_root_uri=analytics_root_uri,
)
verification = await verify_e2e_fixture("interop", session=session, artifact_store=artifact_store)
```

`ensure_e2e_fixture(...)` is the identical function under the name an E2E
workflow would call inline before its own execution
(`fixture bootstrap → known prerequisite state; workflow E2E → behavior
under test; verification → produced outputs`). Both `bootstrap_e2e_fixtures`
and `ensure_e2e_fixture` return `FixtureBootstrapResult` — stable,
JSON-able field names (`fixture_name`, `dataset_id`, `dataset_version`,
`created`, `learning_manifest_*`, `episode_refs`) for scripting; never
scrape log output.

**Create/reuse/verify contract** — a
successful return from `bootstrap_e2e_fixtures`/`ensure_e2e_fixture` always
means the fixture is verified-ready, never merely that a matching record
exists:

```
missing                                  -> create -> verify -> success
existing, matching semantic identity     -> verify -> success only if valid
existing, matching identity, corrupted/
  missing artifact                       -> FixtureVerificationError
existing, incompatible semantic identity -> FixtureConflictError
```

Semantic identity is still `export_id` (interop) / DatasetVersion identity
(core) — computed purely from the golden data + export config, so
it never varies run to run even though real DB IDs and timestamps do. A
matching identity is *necessary* for reuse but no longer *sufficient*:
bootstrap re-reads the persisted Postgres rows and MinIO objects, rechecks
every checksum, and — for `interop` — reopens the snapshot through a real
`SceneOpsDataset` before returning, every single call (not just on first
creation). This makes repeat calls slightly more expensive than pure
existence-checking, but a fixture nothing ever notices is silently broken
is a worse failure mode than a few extra checksum reads.
`verify_e2e_fixture(...)` remains separately callable (e.g. the CLI's
`--verify-only`) to re-check existing state without attempting to
create/reuse anything.

**No automatic repair, partial-write recovery**: `FixtureConflictError`/`FixtureVerificationError` are never
auto-resolved — both require a human decision or a `make local-reset`.
There is also no transaction spanning the MinIO writes and the Postgres
commit for the `interop` fixture: table/manifest bytes are written to
MinIO first, then one commit registers all four `ArtifactRecord`s
together. A crash before that commit leaves nothing persisted in Postgres
(the next bootstrap attempt sees "missing" and retries cleanly, possibly
leaving harmless orphaned MinIO objects that verification simply never
looks at). This is an accepted v1 limitation, not a bug: local E2E state
is disposable, and `make local-reset` is the recovery path, not a repair
API this bootstrap will ever grow.

**CLI / Make surface**:

```bash
make e2e-bootstrap             # core + interop, then verify
make e2e-bootstrap-core
make e2e-bootstrap-interop

uv run python scripts/e2e/bootstrap_e2e_fixtures.py --fixture interop --verify --json
```

Connects from the **host**, like `make test-integration` — the Make
targets override `SCENEOPS_DATABASE_URL`/`MINIO_ENDPOINT_URL` to their
`localhost` forms (not `.env.local`'s container-internal `postgres`/`minio`
hostnames). Common infra config (`SCENEOPS_DATABASE_URL`,
`MINIO_ENDPOINT_URL`, `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`/
`MINIO_BUCKET`) is split from source-specific config
(`E2E_BOOTSTRAP_SOURCE_ROOT_URI`, pointing the nuScenes source check at
`$(CURDIR)/data/raw/nuscenes` on the host) in `makefiles/e2e.mk` — only
`core`/`all` need the latter; `interop` never does. `MINIO_ROOT_USER ?= minioadmin` /
`MINIO_ROOT_PASSWORD ?= minioadmin` / `MINIO_BUCKET ?= sceneops` are
overridable Make variables shared with `make test-integration`, defined
once at the top of the root `Makefile` next to the equivalent
`POSTGRES_*` variables — a caller override
(`MINIO_ROOT_USER=custom make e2e-bootstrap`) still works exactly as
before. Never uses raw SQL or direct boto3/MinIO calls: only real
`sceneops-db` repositories, `ArtifactStore`, and `AnalyticsTableWriter` —
the same abstractions `apps/worker`'s own job handlers use (mirroring
`export_learning_data.py`'s persistence pattern exactly for `interop`).

**Verification** (`--verify`, and internally on every bootstrap call)
independently re-derives the expected `export_id`, fetches the persisted
manifest/table bytes from MinIO, recomputes their checksums, and — for
`interop` — opens the real snapshot through `SceneOpsDataset.open()` and
compares every episode's step count, timestamps, and observation/action
values against the frozen golden expectations. It never writes anything.

**No cleanup command**: same rule as the identity catalog above — nothing
here deletes a fixture, and nothing should until a real need for it
appears. `make local-reset` (destructive, whole-stack) remains the only way
to clear a bootstrapped fixture today.
