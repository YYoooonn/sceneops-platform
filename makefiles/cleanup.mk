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

# Destructive local-state reset lives at `make local-reset` (makefiles/local.mk)
# — this used to be a second, differently-behaved target here under a
# near-identical name (reset-local vs local-reset). Consolidated to one.
