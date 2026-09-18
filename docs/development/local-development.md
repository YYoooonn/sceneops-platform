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
    `${POSTGRES_PORT:-5432}`-style values in `docker-compose.local.yml`
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

`docker-compose.local.yml` should describe *topology* (which services exist,
what they depend on, health checks) and provide only harmless local
fallbacks (`${POSTGRES_DB:-sceneops}`) — not be the primary place
environment-specific values live. Real values live in `.env.local`.

`minio` is intentionally **not** profile-gated, unlike `inference`/
`airflow`/`ros2`/`gpu`/`debug` — it's required infrastructure (the artifact
store backend), not an optional extra, so `api`/`worker-*` can
`depends_on: minio: condition: service_healthy` directly, and a plain
`docker compose down -v` actually reaches `minio-data`.

## Testing

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
`raw-log-scene-building`, `episode-building`, `scenario-curation`,
`detection-evaluation` (mock backend), `analytics-export`, `reliability`.

`e2e-episode-building` reuses the committed MCAP fixture from
`e2e-robot-can-replay` (`apps/worker/tests/fixtures/rosbag/can_replay_scene_0061.mcap`)
rather than needing a live ROS2 sandbox — it runs the
`raw_log_episode_building` pipeline directly against that fixture through
`RosbagAdapter`, so it stays in the default suite despite exercising the
same decode path robot ingestion uses. See
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
