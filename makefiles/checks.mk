# --------------------
# Checks
# --------------------

.PHONY: check-env
check-env:
	chmod +x scripts/checks/check_env.sh
	scripts/checks/check_env.sh

.PHONY: check-imports
check-imports:
	$(COMPOSE) exec api python -c \
		"import app, sceneops_core, sceneops_db, sceneops_storage, celery; print('api imports ok')"
	$(COMPOSE) --profile debug run --rm --entrypoint python worker-cli -c \
		"import sceneops_worker, sceneops_core, sceneops_db, sceneops_storage, celery; print('worker imports ok')"

.PHONY: check-celery
check-celery:
	chmod +x scripts/checks/check_celery_broker.sh
	scripts/checks/check_celery_broker.sh

.PHONY: check-minio
check-minio:
	uv run python scripts/checks/check_minio.py --endpoint "$${MINIO_ENDPOINT:-http://localhost:9000}"
