# --------------------
# Docker Compose (image rebuilds)
#
# Compose builds the api/worker images automatically on first `make
# local-up`. These targets are only for forcing a rebuild after changing
# Python dependencies (pyproject.toml/uv.lock). For status/logs use `make
# status` / `make logs` instead.
# --------------------

.PHONY: compose-build
# worker-pipeline and worker-jobs share one image (sceneops-platform/worker:local,
# see x-worker-common in compose/workers.yaml) — building worker-pipeline
# builds it for both.
compose-build:
	uv lock
	$(COMPOSE) build api worker-pipeline

.PHONY: compose-build-no-cache
compose-build-no-cache:
	uv lock
	$(COMPOSE) build --no-cache api worker-pipeline
