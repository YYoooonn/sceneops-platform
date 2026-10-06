# --------------------
# Setup / Quality
# --------------------

.PHONY: setup
setup:
	chmod +x scripts/setup_dev.sh
	./scripts/setup_dev.sh

.PHONY: uv-sync
uv-sync:
	uv sync --all-packages --group dev

.PHONY: uv-lock
uv-lock:
	uv lock

.PHONY: install-hooks
install-hooks:
	uv run pre-commit install

.PHONY: uninstall-hooks
uninstall-hooks:
	uv run pre-commit uninstall

.PHONY: check
check:
	uv run pre-commit run --all-files

# Infrastructure-independent unit suites, one pytest invocation per suite:
# several suites ship their own `tests/__init__.py` + conftest, which pytest
# cannot register together in one process.
UNIT_TEST_SUITES := apps/worker/tests apps/api/tests apps/inference-server/tests \
	packages/sceneops-analytics/tests packages/sceneops-core/tests \
	packages/sceneops-integrations/tests packages/sceneops-streaming/tests \
	scripts/reference/tests scripts/e2e/tests tests/infrastructure/unit

.PHONY: test
# All infrastructure-independent automated tests -- no Postgres/MinIO/network/
# GPU/model weights required. apps/inference-server/tests is included: every
# test there mocks GroundingDinoModel/ImageResolver -- there is no runtime/
# model-dependent pytest suite; that path is only exercised by
# `make acceptance-grounding-dino`. packages/sceneops-streaming/tests is pure
# wire/schema unit tests (no Kafka broker) -- real-broker behavior is
# `make smoke-streaming`, not this tier. scripts/reference/tests evaluates the
# golden reference contract on synthetic state only; scripts/e2e/tests covers the
# decisions of the read-only equivalence verifier the same way.
test:
	@set -e; for suite in $(UNIT_TEST_SUITES); do \
		echo "=== $$suite"; \
		uv run pytest $$suite -q || exit 1; \
	done

# Real-infrastructure commands run with `-p require_infrastructure`
# (tests/infrastructure/require_infrastructure.py): a skipped test -- stack not
# running, MinIO unreachable -- fails the run instead of passing silently.
REAL_INFRA_PYTEST := uv run pytest -p require_infrastructure

# Suites that commit state of their own (test-integration, test-recovery) run in
# a disposable PostgreSQL database and MinIO bucket that exist only for the run
# (tests/infrastructure/disposable_env.py): created and migrated first, dropped
# afterwards, so the reference environment is never written to and nothing is
# cleaned up row by row. `run` recreates both from scratch, so an interrupted run
# is recovered by running again. The disposable names must match sceneops_test* /
# sceneops-test* and differ from the reference database and bucket
# (POSTGRES_DB / MINIO_BUCKET): the runner refuses anything else and
# require_disposable_environment aborts a pytest session that points elsewhere.
DISPOSABLE_PYTEST := $(REAL_INFRA_PYTEST) -p require_disposable_environment
DISPOSABLE_ENV := POSTGRES_USER=$(POSTGRES_USER) POSTGRES_PASSWORD=$(POSTGRES_PASSWORD) \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) \
	uv run python tests/infrastructure/disposable_env.py
DISPOSABLE_ENV_FLAGS := --database $(TEST_POSTGRES_DB) --bucket $(TEST_MINIO_BUCKET) \
	--reference-database $(POSTGRES_DB) --reference-bucket $(MINIO_BUCKET)
DISPOSABLE_ENV_RUN := $(DISPOSABLE_ENV) run $(DISPOSABLE_ENV_FLAGS) --
# The same, plus a disposable execution runtime (compose/test-runtime.yaml: API,
# Celery workers, Redis and optionally Airflow on that database and bucket) for the
# suites whose subject is orchestration. Usage: $(DISPOSABLE_ENV_RUNTIME) <celery|airflow> -- <cmd>
DISPOSABLE_ENV_RUNTIME := $(DISPOSABLE_ENV) run $(DISPOSABLE_ENV_FLAGS) --env-file $(ENV_FILE) --runtime

INTEGRATION_COMMAND := $(DISPOSABLE_PYTEST) packages/sceneops-db/tests/ packages/sceneops-storage/tests/ -v \
	&& $(DISPOSABLE_PYTEST) -o python_files="*_integration.py" apps/worker/tests packages/sceneops-analytics/tests -v

.PHONY: test-integration
# Real-infrastructure tests of one subsystem each, in the disposable PostgreSQL
# database and MinIO bucket above. Prerequisite: `make local-up` (the Postgres and
# MinIO servers; the reference database and bucket are not touched). sceneops-db/
# storage tests are integration by directory; everywhere else a module is
# integration when its file is named `*_integration.py` (discovered, not listed),
# so a new one cannot be forgotten. Never part of `make test` (those files skip
# there without the environment the runner sets); a skip here is a failure (see
# REAL_INFRA_PYTEST).
test-integration:
	$(DISPOSABLE_ENV_RUN) sh -c '$(INTEGRATION_COMMAND)'

.PHONY: test-infrastructure
# Infrastructure acceptance of the pipeline contracts below the E2E journeys:
# dedup / force / convergence / replacement / blocked resumption / failure
# recovery / concurrent registration, on the Celery orchestrator. These tests
# re-execute pipelines on purpose, and every Job, PipelineRun and report the platform
# appends stays, so they never run on the reference environment: the command
# creates a disposable PostgreSQL database and MinIO bucket, starts an execution
# runtime of its own on them (API, Celery workers, Redis; compose/test-runtime.yaml),
# seeds the one RobotRun of the golden contract it consumes by the production
# create-or-verify path, runs the suite and drops everything (DISPOSABLE_ENVIRONMENT).
# Prerequisite: `make local-up` (the PostgreSQL / MinIO servers and the images) and
# `make reference-data-bootstrap` (the locked recording); the reference database,
# bucket, api, workers and Redis are neither read nor written. The Airflow and
# acquisition-recovery modules are their own targets; a skip here is a failure.
test-infrastructure:
	$(DISPOSABLE_ENV_RUNTIME) celery -- $(DISPOSABLE_PYTEST) tests/infrastructure \
		--ignore=tests/infrastructure/test_airflow_backend.py \
		--ignore=tests/infrastructure/test_acquisition_recovery.py \
		--ignore=tests/infrastructure/test_acquisition_lifecycle_acceptance.py \
		--ignore=tests/infrastructure/unit -v

.PHONY: test-infrastructure-airflow
# The canonical pipelines through the Airflow per-task DAGs, in the same disposable
# environment as test-infrastructure plus a private Airflow (its own metadata
# database, scheduler and webserver; the reference environment's Airflow is not used
# and `airflow-up` is not required). The API of the runtime is started on the
# airflow pipeline backend, so nothing is reconfigured in place. Needs the Docker
# socket (the DAGs start the worker image per task) and builds the Airflow image on
# first use. Fails -- never skips -- when a prerequisite is missing.
test-infrastructure-airflow:
	$(DISPOSABLE_ENV_RUNTIME) airflow -- $(DISPOSABLE_PYTEST) tests/infrastructure/test_airflow_backend.py -v

.PHONY: lint
lint:
	uv run ruff check apps/ packages/

.PHONY: format
format:
	uv run ruff format apps/ packages/
