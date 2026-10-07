# --------------------
# Cleanup
# --------------------

.PHONY: disk-report
# Read-only: host headroom, ./data and cache/, SceneOps volumes, Kafka log, disposable
# test leftovers and what Docker could reclaim. Removes nothing; the cleanup policy is
# docs/development/local-development.md#disk-hygiene.
disk-report:
	@ENV_FILE=$(ENV_FILE) scripts/ops/disk_report.sh

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
