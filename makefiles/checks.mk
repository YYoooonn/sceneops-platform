# --------------------
# Checks
# --------------------

.PHONY: check-env
check-env:
	chmod +x tools/checks/check_env.sh
	tools/checks/check_env.sh

.PHONY: check-imports
check-imports:
	$(COMPOSE) exec api python -c \
		"import app, sceneops_core, sceneops_db, sceneops_storage, sceneops_execution, sceneops_acquisition, celery; print('api imports ok')"
	$(COMPOSE) --profile debug run --rm --entrypoint python worker-cli -c \
		"import sceneops_worker.main, sceneops_core, sceneops_db, sceneops_storage, sceneops_execution, sceneops_acquisition, sceneops_recording, sceneops_scenes, sceneops_episodes, sceneops_derived, sceneops_inference, sceneops_evaluation, sceneops_analytics, celery; print('worker imports ok')"

.PHONY: check-celery
check-celery:
	chmod +x tools/checks/check_celery_broker.sh
	tools/checks/check_celery_broker.sh

.PHONY: check-commands
# The supported command surface is internally consistent: the E2E targets are
# exactly the supported journeys, every advertised target exists, no command or
# script references deleted architecture.
check-commands:
	python3 tools/checks/command_surface.py

.PHONY: check-boundaries
# Dependency direction of the monorepo: apps import packages, packages never import
# apps or tools, the package layering is acyclic, and every import is declared in the
# importing project's pyproject.toml. Static; needs only Python.
check-boundaries:
	python3 tools/checks/import_boundaries.py

.PHONY: check-runtime-boundary
# Raw source is mounted only by the acquisition / reference-preparation
# services: probes every normal runtime service for /data/raw and
# /input/nuscenes and for the paths it genuinely needs. Starts no service.
check-runtime-boundary:
	ENV_FILE=$(ENV_FILE) tools/checks/runtime_source_boundary.sh
