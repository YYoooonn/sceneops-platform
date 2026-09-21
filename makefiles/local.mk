# --------------------
# Local stack
#
# local-up    — idempotent bootstrap: infra -> health -> bucket init ->
#                migrate -> api/workers. Safe to run repeatedly.
# local-down  — stop services, PRESERVE all data (Postgres/Redis/MinIO).
# local-reset — DESTRUCTIVE: delete all local data, rebuild clean.
# --------------------

.PHONY: local-up
local-up: prepare-data
	@echo "--- starting infra (postgres, redis, minio) ---"
	$(COMPOSE) up -d --wait postgres redis minio
	@echo "--- initializing MinIO buckets (idempotent) ---"
	$(COMPOSE) --profile tools run --rm minio-init
	@echo "--- running migrations (idempotent) ---"
	$(MAKE) db-migrate
	@echo "--- starting api + workers ---"
	$(COMPOSE) up -d --wait api
	$(COMPOSE) up -d worker-pipeline worker-jobs
	@echo "--- local-up complete ---"

.PHONY: local-down
local-down:
	$(COMPOSE) --profile worker stop api worker-pipeline worker-jobs postgres redis minio
	$(COMPOSE) --profile worker rm -f api worker-pipeline worker-jobs postgres redis minio
	@echo "Services stopped. Postgres/Redis/MinIO data preserved."
	@echo "Use 'make local-reset' to delete local data instead."

.PHONY: local-reset
# DESTRUCTIVE — deletes Postgres/Redis/MinIO volumes and generated ./data
# artifacts, then rebuilds a clean stack. Interactive confirmation unless
# FORCE=1. See scripts/dev/reset_local_state.sh for the exact steps.
local-reset:
	chmod +x scripts/dev/reset_local_state.sh
	ENV_FILE=$(ENV_FILE) scripts/dev/reset_local_state.sh

.PHONY: logs
logs:
	$(COMPOSE) --profile worker logs -f postgres redis minio api worker-pipeline worker-jobs

.PHONY: status
status:
	$(COMPOSE) --profile worker --profile tools --profile debug ps
