# --------------------
# Acquisition recovery (ADR-008 §3.2, §8 step 12.4)
#
# Both commands are stateless one-shots. `reconcile-once` only observes;
# `reconcile-apply` performs the bounded registration recovery (submit, retry
# within the attempt budget, replace stalled Jobs) through the API's own Job
# path. `recovery-up` runs the two loops (compose/recovery.yaml) that merely
# repeat `publish-pending` and `reconcile --once --apply` -- opt-in, never part
# of local-up, and holding no state of their own.
# --------------------

.PHONY: reconcile-once
reconcile-once:
	$(COMPOSE) exec -T api python -m app.domains.robots.reconciliation --once

.PHONY: reconcile-apply
reconcile-apply:
	$(COMPOSE) exec -T api python -m app.domains.robots.reconciliation --once --apply

.PHONY: artifact-lifecycle-once
# Read-only artifact lifecycle report of the robot_runs/ root (ADR-008 §6):
# referenced / pending / orphan candidate / integrity incident. Classifies only;
# nothing is deleted. Pass a capture scan for O1 / PN-3, e.g.
#   make artifact-lifecycle-once ARGS="--capture-report /path/capture_scan.json"
artifact-lifecycle-once:
	$(COMPOSE) exec -T api python -m app.domains.robots.artifact_lifecycle --once $(ARGS)

.PHONY: recovery-up
recovery-up:
	$(COMPOSE) --profile recovery up -d publication-recovery registration-recovery

.PHONY: recovery-down
# Named services, not `--profile recovery down` bare (see ros2-down).
recovery-down:
	$(COMPOSE) --profile recovery stop publication-recovery registration-recovery
	$(COMPOSE) --profile recovery rm -f publication-recovery registration-recovery

.PHONY: recovery-logs
recovery-logs:
	$(COMPOSE) --profile recovery logs -f publication-recovery registration-recovery

.PHONY: test-recovery
# Fault-injection acceptance of acquisition recovery: real PostgreSQL + MinIO
# (`make local-up`), and a throwaway Redis container plus Celery worker
# subprocesses of the test's own (Docker required) -- killing a worker or
# stopping the broker never touches the dev stack. Needs no canonical baseline.
test-recovery:
	SCENEOPS_DATABASE_URL="postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$${POSTGRES_PORT:-5432}/$(POSTGRES_DB)" \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) \
	MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) \
	MINIO_BUCKET=$(MINIO_BUCKET) \
	uv run pytest tests/infrastructure/test_acquisition_recovery.py -v
