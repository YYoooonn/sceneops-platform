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
  then rebuilds a clean stack. Asks for interactive confirmation unless
  `FORCE=1`. This is the *only* destructive local-state target — it used to
  also exist as `reset-local` under a different, non-restarting behavior;
  that duplicate has been removed.
- `make db-reset` — narrower: wipes only the Postgres volume (not
  Redis/MinIO), then re-migrates. Useful when you just want a clean schema.

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
make e2e               full default-stack workflow suite, requires `make local-up` first
```

- `make test` covers `apps/worker`, `apps/api`, `apps/inference-server`,
  `packages/sceneops-core`, `packages/sceneops-analytics`. Every
  `inference-server` test mocks `GroundingDinoModel`/`ImageResolver` — none
  of it needs GPU, model weights, or a running inference server (confirmed
  during Stabilization Request 4's audit).
- `make test-integration` covers `packages/sceneops-db/tests` (real
  Postgres) and `packages/sceneops-storage/tests` (real MinIO). No pytest
  marker is used to select these — they live in dedicated test directories
  that `make test`'s testpaths never touch, which is sufficient selection on
  its own. Each test gets a fresh session/engine and either rolls back
  (`sceneops-db`, transactional isolation — nothing is ever committed) or
  deletes what it wrote (`sceneops-storage`, via `delete_prefix` under a
  dedicated `_test-integration/` key prefix) — safe to run against the same
  persistent local stack you're developing against.
- On Apple Silicon hosts, running `sceneops-db`'s async engine outside
  Docker requires `greenlet`, which `sqlalchemy`'s own platform-marker-gated
  extra silently excludes there (`aarch64` is listed, macOS's `arm64` isn't)
  — `packages/sceneops-db` now depends on it directly, unconditionally.

## E2E scope

`make e2e` runs the full default-stack suite — every workflow E2E whose
required services are provided by `make local-up` alone:
`api-smoke`, `pipeline-contracts`, `dataset-ingestion`,
`raw-log-scene-building`, `episode-building`, `episode-curation`,
`scenario-curation`, `detection-evaluation` (mock backend),
`analytics-export`, `reliability`.

`e2e-episode-building` reuses the MCAP recording produced by a prior
`e2e-robot-can-replay` run (`data/raw/rosbag/<scene>/<scene>_0.mcap`,
runtime-generated and gitignored, **not** the similarly-named committed
unit-test fixture at `apps/worker/tests/fixtures/rosbag/`) rather than
re-running the ROS2 replay itself — it runs the `raw_log_episode_building`
pipeline directly against that recording through `RosbagAdapter`. This
means a genuinely fresh environment must run `make e2e-robot-can-replay`
(needs the ROS2 sandbox, `--profile ros2`) **at least once** before
`e2e-episode-building` — and therefore `make e2e` — can pass; it is a
one-time data precondition, not a per-run service dependency, so
`e2e-robot-can-replay` itself stays out of the default `make e2e` set. See
[../workflows/robot-run-and-mcap.md](../workflows/robot-run-and-mcap.md).

Not included — each needs infrastructure beyond `make local-up`:
- `e2e-airflow-pipeline` — needs `make airflow-up` + the `api` service
  restarted with `SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow`.
- `e2e-robot-can-replay` — needs the ROS2 sandbox (`--profile ros2`).
- `e2e-detection-evaluation-groundingdino` / `e2e-detection-evaluation-real`
  — needs a real inference server (`make inference-local-up` for CPU or
  `make inference-gpu-up` for GPU); same script either way.

All pipeline-run-creating scripts in the default suite pass `force: true`,
so re-running `make e2e` against an already-populated persistent stack
genuinely re-executes every pipeline rather than silently returning an old
run via execution-key dedup. `e2e-reliability` is the one deliberate
exception — dedup/force *is* what it's testing, so its pipeline-run
creation intentionally omits `force` in the parts that assert dedup/resume
behavior. Assertions throughout are scoped to values returned by the
current run (`pipeline_run_id`, `run_id`s, etc.), not global counts, so they
hold up under a persistent stack's accumulated history — see
`scripts/e2e/e2e_episode_building.sh` or `scripts/e2e/e2e_reliability.sh`
for the clearest examples.

### Canonical identity vs. external source/export identity

`DATASET_ID`/`DATASET_VERSION` mean **SceneOps' own canonical identity
only** — never constrained by what an external format's SDK happens to
require. This wasn't always true: Request 3.2A had to keep `DATASET_VERSION`
pinned to the real `v1.0-mini` for nuScenes-backed workflows, because
`ingest_scenes`/`build_scenes` passed `dataset_version` straight into the
real `nuscenes-devkit` `NuScenes(version=..., dataroot=...)` loader, which
requires it to be the literal on-disk version folder name (found by
actually running `make e2e-pipeline-contracts` against a renamed version
and hitting `Database version not found: /data/raw/nuscenes/test-v1`).

Request 3.2B separated the two: `IngestScenesJobParams`/`BuildScenesJobParams`
carry an explicit `source_format_version` field, and the nuScenes SDK
(`apps/worker/sceneops_worker/jobs/dataset/ingest_scenes.py`,
`datasets/ingestion/nuscenes_raw_log.py`) reads only that, never
`dataset_version` — so `DATASET_VERSION` is finally free to be `test-v1`
everywhere. Request 3.2B.1 then removed the short-lived
`source_format_version or dataset_version` fallback entirely:
`source_format_version` is **required** whenever the source format needs
one (nuScenes) — enforced by a pydantic validator on both job params
classes at job-creation time — so a caller that omits it gets a clear
validation error instead of silently reusing `dataset_version`. Local
SceneOps state may always be reset, so no backward compatibility with the
old overloaded behavior is preserved. The shared conceptual shape for "a
dataset outside SceneOps' canonical model" — covering both this nuScenes
import source and a future LeRobot/RLDS export target — is
`ExternalDatasetRef` (`sceneops_core.datasets.ExternalDatasetRef`):
`format`/`format_version`/`uri` plus optional `external_name`/
`external_revision`/`checksum`. Import vs. export is a property of the
operation, not the ref's shape.

### The E2E fixture catalog: `sceneops-e2e-v1`

Request 3.2A gave every workflow its own derived dataset identity — safe,
but it meant one canonical Dataset/DatasetVersion per workflow even where
nothing about the data required that. Request 3.2B replaces that with two
shared logical fixtures, resolved via `scripts/e2e/lib.sh`'s
`resolve_e2e_fixture <name>`:

- **`core`** — `DATASET_ID=test-e2e-core`, `DATASET_VERSION=test-v1`,
  external source `SOURCE_FORMAT=nuscenes`/`SOURCE_FORMAT_VERSION=v1.0-mini`/
  `SOURCE_ROOT_URI=/data/raw/nuscenes`. Used by `pipeline-contracts`,
  `dataset-ingestion`, `scenario-curation`, `detection-evaluation`,
  `analytics-export`, `reliability`, `airflow-pipeline`, `episode-building`,
  and `episode-curation`. Scene and Episode families coexist on one
  DatasetVersion by design — each owns an independent summary sub-object
  that never overwrites the other's (see
  `packages/sceneops-core/tests/test_dataset_version_summaries.py`);
  `episode-building` additionally needs the MCAP fixture from
  `e2e-robot-can-replay` (see above), unrelated to this canonical identity.
- **`interop`** — `DATASET_ID=test-e2e-interop`, `DATASET_VERSION=test-v1`.
  Source: the deterministic golden learning fixture built by
  `sceneops_analytics.testing.interop_dataset` (Request 3.2), for
  external-adapter/interoperability round-trip tests. Python-only today —
  no shell E2E ingestion path exists for it yet.

`raw-log-scene-building` deliberately stays its own identity
(`DATASET_ID=test-e2e-raw-log`), **outside** the catalog: it produces
non-ground-truth scenes that measurably drag down `core`'s aggregate
`/quality` readiness if they share one DatasetVersion (see
`makefiles/e2e.mk`'s comment on that target) — a real, previously-discovered
data-requirement conflict, not an oversight. It still resolves through the
same `resolve_e2e_fixture raw-log` call for consistency, and shares `core`'s
`SOURCE_FORMAT_VERSION` (both read the same physical nuScenes mini fixture).

`api-smoke` sits outside the catalog entirely — always a fresh
`test-e2e-smoke-<timestamp>`, never overridable, since it never accepts a
caller-supplied dataset.

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
