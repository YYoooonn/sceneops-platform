# nuScenes integration environment

Isolated, reproducible dependency environment for
`sceneops_integrations.nuscenes` (SceneOps V2 Request 4.4, isolated into
this project/container in Request 4.5).

## Why this exists

`sceneops-integrations` (a `[tool.uv.workspace]` member) does **not**
declare `nuscenes-devkit` as a dependency of itself. `nuscenes-devkit`
remains, for now, also a direct `apps/worker` dependency -- its separate
legacy/direct `SceneManifest` ingestion path (`ingest_scenes.py`,
`JobType.INGEST_SCENES`) still calls `from nuscenes.nuscenes import
NuScenes` directly, independent of the raw-log path this project replaces
(see `apps/worker/pyproject.toml`'s own comment). So unlike
`../lerobot-integration` (whose isolation exists to dodge lerobot's
`numpy>=2` vs. nuscenes-devkit's `numpy<2` pin), isolating nuScenes here is
not primarily about a version conflict -- it's about giving the **container
image** a minimal, reproducible, DB/Celery-free dependency closure (Request
4.5 §1/§7). Building straight off the root workspace's `uv.lock` would pull
in `sceneops-db`, Celery, `onnxruntime`, and everything else `apps/worker`
happens to need, none of which this runtime may ever depend on.

This project is a separate, non-workspace-member uv project with its own
independent `uv.lock`. It depends on the **existing**
`sceneops-core`/`sceneops-storage`/`sceneops-integrations` code via
editable path sources (no code is duplicated), plus `nuscenes-devkit`
directly.

## Commands

```bash
# from the repo root
make nuscenes-sync    # cd tools/nuscenes-integration && uv sync --group dev --locked
make nuscenes-lock    # re-lock after changing this project's or nuscenes-devkit's own pin
make nuscenes-image           # docker build ... -t sceneops-platform/nuscenes-integration:local
make nuscenes-container-smoke # build IntegrationRequest -> run container -> verify IntegrationResult

# equivalent, run directly
cd tools/nuscenes-integration
uv sync --group dev --locked
```

The base SceneOps workspace is unaffected and uses its normal commands:

```bash
uv sync --all-packages --group dev --locked
make test
```

## Notes

- `sceneops_integrations.nuscenes.raw_log`/`runtime.py`'s own tests
  (`packages/sceneops-integrations/tests/test_nuscenes_runtime.py`) already
  run from the *base* workspace venv via `make test` -- `nuscenes-devkit`
  happens to still be installed there too (apps/worker's own dependency,
  see above), so a second, isolated `make nuscenes-test` target isn't
  needed the way `make lerobot-test` is (lerobot is never installed in the
  base venv at all).
- This container never opens a DB session and never registers an
  `ArtifactRecord` -- it only reads nuScenes source data and writes
  `RawLogManifest`/`RawLogFrameIndex` artifacts via `ArtifactStore`. The
  main worker remains solely responsible for canonical registration
  (`sceneops_worker.jobs.dataset.build_scenes`).
