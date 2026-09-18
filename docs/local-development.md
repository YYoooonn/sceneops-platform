# Local development

The canonical way to run SceneOps locally. `make help` is the quick
reference; this doc explains the *why* behind it.

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

## E2E scope

`make e2e` runs 4 scripts (`api-smoke`, `dataset-ingestion`,
`detection-evaluation`, `pipeline-contracts`) — **not** the full E2E surface.
`e2e-raw-log-scene-building`, `e2e-episode-building`, `e2e-scenario-curation`,
`e2e-analytics-export`, `e2e-reliability`, `e2e-airflow-pipeline`,
`e2e-robot-can-replay` are real, separately-invokable targets not included
in the default `make e2e` aggregate. Expanding that aggregate to match is
tracked as a later stabilization request — this doc and `make help`
describe the current, true scope rather than the aspirational one.
