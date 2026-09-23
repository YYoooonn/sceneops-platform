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

# --------------------
# LeRobot round-trip E2E (SceneOps V2 Request 3.4)
# --------------------
#
# Real persistent "interop" fixture (Postgres + MinIO) -> SceneOpsDataset ->
# LeRobotDatasetAdapter (run from tools/lerobot-integration's isolated
# venv) -> real LeRobot v3 dataset -> official LeRobot reader -> golden
# comparison. See scripts/e2e/e2e_lerobot_roundtrip.sh's own header for the
# full two-process/two-venv flow. Not part of `make e2e` (optional,
# requires `make lerobot-sync` once first) -- same convention as the other
# "Optional environment E2E" targets in makefiles/e2e.mk.
#
# Uses e2e.mk's E2E_BOOTSTRAP_ENV (same Postgres/MinIO host-side connection
# convention as `make e2e-bootstrap-interop`) -- deliberately does NOT run
# `lerobot-sync` as a prerequisite: `uv run` inside the script already does
# a fast, cheap up-to-date check against the committed lockfile on its own
# (matching every other `make e2e-*`/`make test` target's own use of `uv
# run` with no separate explicit sync step), so this avoids forcing a full
# dependency resolution on every run.
.PHONY: e2e-lerobot
e2e-lerobot:
	chmod +x scripts/e2e/e2e_lerobot_roundtrip.sh
	$(E2E_BOOTSTRAP_ENV) scripts/e2e/e2e_lerobot_roundtrip.sh
