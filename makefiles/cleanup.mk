# --------------------
# Cleanup
# --------------------

.PHONY: disk-report
# Read-only: host headroom, ./data and cache/, SceneOps volumes, Kafka log, disposable
# test leftovers and what Docker could reclaim. Removes nothing; the cleanup policy is
# docs/development/local-development.md#disk-hygiene.
disk-report:
	@ENV_FILE=$(ENV_FILE) tools/dev/disk_report.sh

.PHONY: capacity-preflight
# Read-only: would a reference workload fit? MODE=recording_import|streaming_acquisition
# (default streaming_acquisition) over FIXTURE=<id> or REFERENCE_SCOPE=<scope>, estimated
# from the corpus lock against the Docker VM's free space (tools/dev/capacity_preflight.py;
# the baseline bootstraps run the same check before their first write). Exit 1: it does not fit.
capacity-preflight:
	@python3 tools/dev/capacity_preflight.py --mode $(or $(MODE),streaming_acquisition) \
		$(if $(FIXTURE),--fixture $(FIXTURE),--scope $(REFERENCE_SCOPE))

.PHONY: prepare-data
prepare-data:
	mkdir -p data/raw data/runs data/artifacts data/inputs cache/hf

.PHONY: clean-artifacts
clean-artifacts:
	rm -rf data/runs/* data/artifacts/*
	$(MAKE) prepare-data

.PHONY: clean-python
clean-python:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".mypy_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".ruff_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true

# Destructive local-state reset lives at `make local-reset` (makefiles/local.mk).
