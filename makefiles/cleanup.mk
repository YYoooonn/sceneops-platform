# --------------------
# Cleanup
# --------------------

.PHONY: prepare-data
prepare-data:
	mkdir -p data/raw data/datasets data/runs data/models data/artifacts cache/hf

.PHONY: clean-artifacts
clean-artifacts:
	rm -rf data/datasets/* data/runs/* data/models/* data/artifacts/*
	$(MAKE) prepare-data

.PHONY: clean-python
clean-python:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".pytest_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".mypy_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name ".ruff_cache" -type d -prune -exec rm -rf {} + 2>/dev/null || true

.PHONY: reset-local
reset-local:
	chmod +x scripts/dev/reset_local_state.sh
	scripts/dev/reset_local_state.sh
