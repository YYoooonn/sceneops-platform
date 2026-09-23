# LeRobot integration environment

Isolated, reproducible dependency environment for
`sceneops_analytics.external_adapters.lerobot` (SceneOps V2 Request 3.3,
dependency-isolated in Request 3.3A).

## Why this exists

`sceneops-analytics` (a `[tool.uv.workspace]` member) does **not** declare
`lerobot` as an optional extra of itself. lerobot 0.4.4's dependency graph
needs `numpy>=2`, which is permanently incompatible with `apps/worker`'s
`nuscenes-devkit` pin in the same universal-resolution `uv.lock`
(`nuscenes-devkit`'s only `numpy<2`-compatible fallback needs a `matplotlib`
release with no Python 3.11 wheels on PyPI at all). `[tool.uv.conflicts]`
was tried first and does not cleanly fix this -- see
`../../packages/sceneops-analytics/pyproject.toml`'s comment for the full
chain.

This project is a separate, non-workspace-member uv project with its own
independent `uv.lock`. It depends on the **existing**
`sceneops-core`/`sceneops-storage`/`sceneops-analytics` code via editable
path sources (no code is duplicated), plus `lerobot` directly. Its
dependency graph never includes `apps/worker` or `nuscenes-devkit`, so the
conflict above cannot occur here.

## Commands

```bash
# from the repo root
make lerobot-sync   # cd tools/lerobot-integration && uv sync --group dev --locked
make lerobot-test    # runs packages/sceneops-analytics/tests/test_lerobot_adapter.py
make lerobot-lock   # re-lock after changing this project's or lerobot's own pin

# equivalent, run directly
cd tools/lerobot-integration
uv sync --group dev --locked
uv run pytest -c pyproject.toml ../../packages/sceneops-analytics/tests/test_lerobot_adapter.py -v
```

The base SceneOps workspace is unaffected and uses its normal commands:

```bash
uv sync --all-packages --group dev --locked
make test
```

## Notes

- `-c pyproject.toml` is required on the `pytest` invocation: the test file
  lives under `packages/sceneops-analytics/tests/`, which is not a
  descendant of this directory, so pytest's config-file discovery would
  otherwise walk up to the root workspace's `pyproject.toml` instead of
  this project's.
- `test_lerobot_adapter.py` uses `pytest.importorskip("lerobot")`, so it is
  skipped (not an error) when run from the base workspace venv, where
  lerobot is intentionally not installed.
