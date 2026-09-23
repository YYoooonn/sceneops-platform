# --------------------
# LeRobot integration (SceneOps V2 Request 3.3/3.3A)
# --------------------
#
# sceneops_analytics.external_adapters.lerobot lives in the main
# sceneops-analytics package, but its `lerobot` dependency is deliberately
# NOT locked into the main uv workspace's uv.lock -- lerobot 0.4.4's own
# dependency graph needs numpy>=2, which permanently conflicts with
# apps/worker's nuscenes-devkit pin in a shared universal-resolution
# lockfile (see tools/lerobot-integration/pyproject.toml's comment for the
# full chain). tools/lerobot-integration/ is a separate, non-workspace-member
# uv project with its own independent uv.lock that depends on the *existing*
# sceneops-core/sceneops-storage/sceneops-analytics code via editable path
# sources, plus lerobot directly -- nothing is duplicated, only re-locked in
# isolation.

.PHONY: lerobot-sync
lerobot-sync:
	cd tools/lerobot-integration && uv sync --group dev --locked

.PHONY: lerobot-lock
lerobot-lock:
	cd tools/lerobot-integration && uv lock

.PHONY: lerobot-test
lerobot-test:
	cd tools/lerobot-integration && uv run pytest -c pyproject.toml ../../packages/sceneops-analytics/tests/test_lerobot_adapter.py -v
