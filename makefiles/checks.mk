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

.PHONY: check-commands
# The supported command surface is internally consistent: the E2E targets are
# exactly the supported journeys, every advertised target exists, no command or
# script references deleted architecture.
check-commands:
	python3 scripts/checks/command_surface.py

.PHONY: check-runtime-boundary
# Raw source is mounted only by the acquisition / reference-preparation
# services: probes every normal runtime service for /data/raw and
# /input/nuscenes and for the paths it genuinely needs. Starts no service.
check-runtime-boundary:
	ENV_FILE=$(ENV_FILE) scripts/checks/runtime_source_boundary.sh
