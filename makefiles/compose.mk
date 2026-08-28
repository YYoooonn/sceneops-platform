# --------------------
# Docker Compose (raw)
# --------------------

.PHONY: compose-build
compose-build:
	uv lock
	docker compose -f $(COMPOSE_FILE) build api worker-pipeline

.PHONY: compose-build-no-cache
compose-build-no-cache:
	uv lock
	docker compose -f $(COMPOSE_FILE) build --no-cache api worker-pipeline

.PHONY: compose-up
compose-up: prepare-data minio-up
	docker compose -f $(COMPOSE_FILE) up -d postgres redis api worker-pipeline worker-jobs

.PHONY: compose-down
compose-down:
	docker compose -f $(COMPOSE_FILE) down

.PHONY: compose-down-volumes
compose-down-volumes:
	docker compose -f $(COMPOSE_FILE) down -v

.PHONY: compose-logs
compose-logs:
	docker compose -f $(COMPOSE_FILE) logs -f postgres redis api worker-pipeline worker-jobs

.PHONY: compose-ps
compose-ps:
	docker compose -f $(COMPOSE_FILE) ps
