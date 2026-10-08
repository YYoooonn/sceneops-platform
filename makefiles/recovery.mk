# --------------------
# Acquisition recovery (ADR-008 §3.2, §8 steps 12.4-12.6) and job lease recovery
#
# Every command is a stateless one-shot. `reconcile-once` only observes;
# `reconcile-apply` performs the bounded registration recovery (submit, retry
# within the attempt budget, replace stalled Jobs) through the API's own Job
# path; `execution-recovery` requeues the RUNNING Jobs whose worker stopped
# renewing its lease and re-sends the job / advance messages durable state has
# waited on too long. `recovery-up` runs the three loops (compose/recovery.yaml)
# that merely repeat `publish-pending`, `reconcile --once --apply` and
# `sceneops-worker recover` -- opt-in, never part of local-up, and holding no
# state of their own.
# --------------------

.PHONY: reconcile-once
reconcile-once:
	$(COMPOSE) exec -T api python -m app.domains.robots.reconciliation --once

.PHONY: reconcile-apply
reconcile-apply:
	$(COMPOSE) exec -T api python -m app.domains.robots.reconciliation --once --apply

.PHONY: execution-recovery
# One execution recovery pass (sceneops-worker recover): requeue each RUNNING Job
# whose lease has passed (fail it once its claim budget is spent), then re-send
# run_job / advance for QUEUED Jobs and waiting PipelineRuns whose message is
# overdue (ARGS="--resend-after-seconds N").
execution-recovery:
	$(COMPOSE) exec -T worker-jobs sceneops-worker recover $(ARGS)

.PHONY: execution-status
# Read-only execution health from durable state (sceneops-worker execution-status):
# backlog and oldest queued Job, running Jobs and heartbeat age, what active
# PipelineRuns wait on, and over a window throughput, latency percentiles,
# failures and recovery actions by type, plus the broker's queue depths. One JSON
# document; nothing is stored. ARGS="--window-seconds 900" / "--no-broker".
execution-status:
	$(COMPOSE) exec -T worker-jobs sceneops-worker execution-status $(ARGS)

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
	$(COMPOSE) --profile recovery up -d publication-recovery registration-recovery execution-recovery

.PHONY: recovery-down
# Named services, not `--profile recovery down` bare (see ros2-down).
recovery-down:
	$(COMPOSE) --profile recovery stop publication-recovery registration-recovery execution-recovery
	$(COMPOSE) --profile recovery rm -f publication-recovery registration-recovery execution-recovery

.PHONY: recovery-logs
recovery-logs:
	$(COMPOSE) --profile recovery logs -f publication-recovery registration-recovery execution-recovery

# The recovery suites: acquisition recovery, one fault per test
# (test_acquisition_recovery.py), its full-lifecycle acceptance
# (test_acquisition_lifecycle_acceptance.py), and worker loss under the Job
# ownership lease (test_job_lease_recovery.py) and lost broker messages
# (test_lost_dispatch_recovery.py).
RECOVERY_TESTS ?= tests/infrastructure/test_acquisition_recovery.py \
	tests/infrastructure/test_acquisition_lifecycle_acceptance.py \
	tests/infrastructure/test_job_lease_recovery.py \
	tests/infrastructure/test_lost_dispatch_recovery.py

.PHONY: infra-suite-recovery
# Fault-injection acceptance of acquisition recovery (`make test-infrastructure
# SUITE=recovery`): real PostgreSQL + MinIO in the disposable database and bucket of
# `make test-integration` (the servers of `make local-up`; the reference database and
# bucket are not touched), and a throwaway Redis container plus Celery worker
# subprocesses of the test's own (Docker required) -- killing a worker or stopping
# the broker never touches the dev stack. Needs no canonical baseline.
# The production commands run as subprocesses: publish-pending, reconcile --once
# --apply and the read-only acquisition status. One module alone:
#   make test-infrastructure SUITE=recovery RECOVERY_TESTS=tests/infrastructure/test_acquisition_lifecycle_acceptance.py
infra-suite-recovery:
	$(DISPOSABLE_ENV_RUN) $(DISPOSABLE_PYTEST) $(RECOVERY_TESTS) -v
