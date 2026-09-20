# --------------------
# MinIO
#
# minio is a default (non-profile-gated) service — started as part of
# `make local-up`. These targets are for operating on it standalone.
# --------------------

.PHONY: minio-up
minio-up:
	$(COMPOSE) up -d --wait minio

.PHONY: minio-down
minio-down:
	$(COMPOSE) stop minio

.PHONY: minio-init
# Idempotent bucket bootstrap (mc mb --ignore-existing). Invoked
# automatically by `make local-up` — run standalone to re-sync
# ./data/raw into MinIO or recreate a bucket without a full local-up.
minio-init:
	$(COMPOSE) --profile tools run --rm minio-init

.PHONY: minio-logs
minio-logs:
	$(COMPOSE) logs -f minio

.PHONY: minio-console
minio-console:
	@echo "MinIO API:     http://localhost:$${MINIO_API_PORT:-9000}"
	@echo "MinIO Console: http://localhost:$${MINIO_CONSOLE_PORT:-9001}"
