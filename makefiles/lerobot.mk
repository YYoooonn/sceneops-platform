# --------------------
# LeRobot integration
# --------------------
#
# sceneops_analytics.external_adapters.lerobot lives in the main
# sceneops-analytics package, but its `lerobot` dependency is deliberately
# NOT locked into the main uv workspace's uv.lock -- lerobot 0.4.4's own
# dependency graph needs numpy>=2, which permanently conflicts with
# apps/worker's pins in a shared universal-resolution lockfile (see
# integrations/lerobot/pyproject.toml's comment for the full chain).
# integrations/lerobot/ is a separate, non-workspace-member uv project
# with its own independent uv.lock that depends on the *existing*
# sceneops-core/sceneops-storage/sceneops-analytics code via editable path
# sources, plus lerobot directly -- nothing is duplicated, only re-locked in
# isolation.

.PHONY: lerobot-sync
lerobot-sync:
	cd integrations/lerobot && uv sync --group dev --locked

.PHONY: lerobot-lock
lerobot-lock:
	cd integrations/lerobot && uv lock

.PHONY: lerobot-test
# The adapter and container-entrypoint unit tests, in the isolated environment.
lerobot-test:
	cd integrations/lerobot && uv run pytest -c pyproject.toml ../../packages/sceneops-analytics/tests/test_lerobot_adapter.py ../../packages/sceneops-analytics/tests/test_lerobot_entrypoint.py -v

# --------------------
# LeRobot integration container
# --------------------
#
# The isolated integrations/lerobot environment as a reproducible
# container (compose/lerobot.yaml) that executes the frozen
# IntegrationRequest -> IntegrationResult contract
# (sceneops_core.integration_runtime): operation=EXPORT/format=lerobot. Built
# from the REPO ROOT context (its editable path sources reach outside
# integrations/lerobot/), with its own committed uv.lock -- the root
# uv.lock is never touched by anything below, and this image never installs
# sceneops-db or worker code. `make e2e-episode-learning` runs it on a real
# learning export and reads the result back with the official LeRobot reader.

.PHONY: lerobot-image
lerobot-image:
	$(COMPOSE) --profile lerobot build lerobot-integration
