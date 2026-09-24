# nuScenes integration environment

Isolated, reproducible dependency environment for
`sceneops_integrations.nuscenes` (SceneOps V2 Request 4.4, isolated into
this project/container in Request 4.5, HTTP transport in Request 4.6A).
Serves two INGEST capabilities behind the same `/execute` transport
(Request 4.6B):

- `mode=raw_log` -- `BuildScenesJobHandler`'s raw-log ingest, feeding the
  raw-log -> `BUILD_SCENES` segmentation/sampling pipeline.
- `mode=scene_manifest` -- `IngestScenesJobHandler`'s direct canonical
  Scene ingest, one `SceneManifest` per real nuScenes scene, including
  ground-truth annotations. This is the repository's only source of
  ground-truth-bearing scenes (consumed by `sceneops_worker.evaluation.
  detection`/`sceneops_worker.jobs.scenarios`) -- migrated here, not
  removed, when its formerly in-process `nuscenes-devkit` call was
  audited (see `sceneops_integrations.nuscenes.scene_ingest`'s own
  docstring).

## Why this exists

`sceneops-integrations` (a `[tool.uv.workspace]` member) does **not**
declare `nuscenes-devkit` as a dependency of itself. As of Request 4.6B,
`apps/worker` no longer depends on `nuscenes-devkit` at all -- both
nuScenes job handlers (`BuildScenesJobHandler`, `IngestScenesJobHandler`)
run through this project's HTTP service instead of importing the SDK
in-process. So unlike `../lerobot-integration` (whose isolation exists to
dodge lerobot's `numpy>=2` vs. nuscenes-devkit's `numpy<2` pin), isolating
nuScenes here was never primarily about a version conflict -- it's about
giving the **container image** a minimal, reproducible, DB/Celery-free
dependency closure (Request 4.5 §1/§7). Building straight off the root
workspace's `uv.lock` would pull in `sceneops-db`, Celery, `onnxruntime`,
and everything else `apps/worker` happens to need, none of which this
runtime may ever depend on.

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

- As of Request 4.6B, `nuscenes-devkit` is no longer installed in the
  *base* workspace venv at all (apps/worker no longer depends on it) --
  `packages/sceneops-integrations/tests/test_nuscenes_runtime.py`/
  `test_nuscenes_scene_ingest.py`/`test_nuscenes_service.py` are skipped,
  not failed, there (`pytest.importorskip("nuscenes")`, same convention
  `test_lerobot_adapter.py` already used for `lerobot`). Run them for
  real from this project's own venv:
  ```bash
  make nuscenes-sync
  cd tools/nuscenes-integration
  uv run pytest ../../packages/sceneops-integrations/tests/
  ```
- This container never opens a DB session and never registers an
  `ArtifactRecord` -- it only reads nuScenes source data and writes
  `RawLogManifest`/`RawLogFrameIndex`/`SceneManifest` artifacts via
  `ArtifactStore`. The main worker remains solely responsible for
  canonical registration (`sceneops_worker.jobs.dataset.build_scenes`,
  `sceneops_worker.jobs.dataset.ingest_scenes`).
