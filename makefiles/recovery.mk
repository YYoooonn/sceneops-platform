# --------------------
# Acquisition recovery (ADR-008 §3.2, §8 steps 12.4-12.6)
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

.PHONY: acquisition-status
# Read-only operational report (ADR-008 §7): one derived AcquisitionStatus per run
# plus the aggregates -- runs by stage and health, pending / failed registrations,
# oldest stalled work, retry budget use, referenced / pending / orphan-candidate
# bytes, integrity incidents. Nothing is stored. The platform never mounts the
# capture volume, so capture stages need a scan (`recording scan-capture`):
#   make acquisition-status ARGS="--summary-only"
#   make acquisition-status ARGS="--capture-report /path/capture_scan.json"
acquisition-status:
	$(COMPOSE) exec -T api python -m app.domains.robots.acquisition_status --once $(ARGS)

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

# Both recovery suites: one fault per test (test_acquisition_recovery.py) and the
# full-lifecycle acceptance (test_acquisition_lifecycle_acceptance.py).
RECOVERY_TESTS ?= tests/infrastructure/test_acquisition_recovery.py \
	tests/infrastructure/test_acquisition_lifecycle_acceptance.py

.PHONY: test-recovery
# Fault-injection acceptance of acquisition recovery: real PostgreSQL + MinIO in
# the disposable database and bucket of `make test-integration` (the servers of
# `make local-up`; the reference database and bucket are not touched), and a
# throwaway Redis container plus Celery worker subprocesses of the test's own
# (Docker required) -- killing a worker or stopping the broker never touches the
# dev stack. Needs no canonical baseline.
# The production commands run as subprocesses: publish-pending, reconcile --once
# --apply and the read-only acquisition status. One suite alone:
#   make test-recovery RECOVERY_TESTS=tests/infrastructure/test_acquisition_lifecycle_acceptance.py
test-recovery:
	$(DISPOSABLE_ENV_RUN) $(DISPOSABLE_PYTEST) $(RECOVERY_TESTS) -v
