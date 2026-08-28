# --------------------
# Local stack
# --------------------

.PHONY: local-up
local-up: prepare-data minio-up
	docker compose -f $(COMPOSE_FILE) up -d postgres redis
	docker compose -f $(COMPOSE_FILE) up -d api worker-pipeline worker-jobs

.PHONY: local-down
local-down: minio-down
	docker compose -f $(COMPOSE_FILE) down api worker-pipeline worker-jobs
	docker compose -f $(COMPOSE_FILE) down postgres redis

.PHONY: local-reset
local-reset:
	docker compose -f $(COMPOSE_FILE) --profile minio down -v
	$(MAKE) clean-artifacts
	$(MAKE) local-up

.PHONY: local-logs
local-logs:
	docker compose -f $(COMPOSE_FILE) logs -f postgres redis minio api worker-pipeline worker-jobs

.PHONY: local-ps
local-ps:
	docker compose -f $(COMPOSE_FILE) --profile worker --profile minio ps
