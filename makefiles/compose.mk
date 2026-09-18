# --------------------
# Docker Compose (raw escape hatches)
#
# For normal local development use `make local-up` / `local-down` /
# `local-reset` / `status` / `logs` instead — these exist for cases those
# don't cover (rebuilding images, inspecting a service outside the default
# profile set).
# --------------------

.PHONY: compose-build
# worker-pipeline and worker-jobs share one image (sceneops-platform/worker:local,
# see x-worker-common in docker-compose.local.yml) — building worker-pipeline
# builds it for both.
compose-build:
	uv lock
	$(COMPOSE) build api worker-pipeline

.PHONY: compose-build-no-cache
compose-build-no-cache:
	uv lock
	$(COMPOSE) build --no-cache api worker-pipeline

.PHONY: compose-logs
compose-logs:
	$(COMPOSE) --profile worker --profile tools --profile debug logs -f

.PHONY: compose-ps
compose-ps:
	$(COMPOSE) --profile worker --profile tools --profile debug ps
