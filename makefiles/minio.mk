# --------------------
# MinIO
# --------------------

.PHONY: minio-up
minio-up:
	docker compose -f $(COMPOSE_FILE) --profile minio up -d minio

.PHONY: minio-down
minio-down:
	docker compose -f $(COMPOSE_FILE) --profile minio down minio

minio-migrate:
	docker compose -f $(COMPOSE_FILE) --profile minio run --rm minio-init

.PHONY: minio-logs
minio-logs:
	docker compose -f $(COMPOSE_FILE) --profile minio logs -f minio

.PHONY: minio-console
minio-console:
	@echo "MinIO API:     http://localhost:9000"
	@echo "MinIO Console: http://localhost:9001"
