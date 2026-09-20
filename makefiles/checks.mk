# --------------------
# Checks
# --------------------

.PHONY: check-env
check-env:
	chmod +x scripts/checks/check_env.sh
	scripts/checks/check_env.sh

.PHONY: check-imports
check-imports:
	chmod +x scripts/checks/check_python_imports.sh
	scripts/checks/check_python_imports.sh

.PHONY: check-celery
check-celery:
	chmod +x scripts/checks/check_celery_broker.sh
	scripts/checks/check_celery_broker.sh

.PHONY: check-minio
check-minio:
	chmod +x scripts/checks/check_minio.sh
	scripts/checks/check_minio.sh
