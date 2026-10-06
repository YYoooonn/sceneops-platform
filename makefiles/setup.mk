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
	scripts/reference/tests

.PHONY: test
# All infrastructure-independent automated tests -- no Postgres/MinIO/network/
# GPU/model weights required. apps/inference-server/tests is included: every
# test there mocks GroundingDinoModel/ImageResolver -- there is no runtime/
# model-dependent pytest suite; that path is only exercised by
# `make acceptance-grounding-dino`. packages/sceneops-streaming/tests is pure
# wire/schema unit tests (no Kafka broker) -- real-broker behavior is
# `make smoke-streaming`, not this tier. scripts/reference/tests evaluates the
# golden reference contract on synthetic state only.
test:
	@set -e; for suite in $(UNIT_TEST_SUITES); do \
		echo "=== $$suite"; \
		uv run pytest $$suite -q || exit 1; \
	done

# Real-infrastructure commands run with `-p require_infrastructure`
# (tests/infrastructure/require_infrastructure.py): a skipped test -- stack not
# running, MinIO unreachable -- fails the run instead of passing silently.
REAL_INFRA_PYTEST := uv run pytest -p require_infrastructure

.PHONY: test-integration
# Real-infrastructure tests of one subsystem each. Prerequisite: `make
# local-up` (Postgres + MinIO). sceneops-db/storage tests are integration by
# directory; everywhere else a module is integration when its file is named
# `*_integration.py` (discovered, not listed), so a new one cannot be forgotten.
# Never part of `make test` (those files skip there without the variables set
# below); a skip here is a failure (see REAL_INFRA_PYTEST).
test-integration:
	SCENEOPS_DATABASE_URL="postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$${POSTGRES_PORT:-5432}/$(POSTGRES_DB)" \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) \
	MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) \
	MINIO_BUCKET=$(MINIO_BUCKET) \
	$(REAL_INFRA_PYTEST) packages/sceneops-db/tests/ packages/sceneops-storage/tests/ -v
	SCENEOPS_DATABASE_URL="postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$${POSTGRES_PORT:-5432}/$(POSTGRES_DB)" \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) \
	MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) \
	MINIO_BUCKET=$(MINIO_BUCKET) \
	$(REAL_INFRA_PYTEST) -o python_files='*_integration.py' apps/worker/tests packages/sceneops-analytics/tests -v

.PHONY: test-infrastructure
# Infrastructure acceptance of the pipeline contracts below the E2E journeys:
# dedup / force / convergence / replacement / blocked resumption / failure
# recovery / concurrent registration. Runs against the live stack (`make
# local-up`); builds on the canonical baseline (canonical-bootstrap) and writes
# only into throwaway DatasetVersions. The Airflow and acquisition-recovery
# modules are their own targets (test-infrastructure-airflow, test-recovery); a
# skip here is a failure.
test-infrastructure: canonical-bootstrap
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) ENV_FILE=$(ENV_FILE) \
	$(REAL_INFRA_PYTEST) tests/infrastructure \
		--ignore=tests/infrastructure/test_airflow_backend.py \
		--ignore=tests/infrastructure/test_acquisition_recovery.py \
		--ignore=tests/infrastructure/test_acquisition_lifecycle_acceptance.py -v

.PHONY: test-infrastructure-airflow
# The same canonical pipelines through the Airflow per-task DAGs. Requires:
# `make airflow-up`, and the api service restarted with
# SCENEOPS_API_EXECUTION__PIPELINE_BACKEND=airflow (a process-startup
# setting, not automatable from here). Fails -- never skips -- when the API,
# Airflow backend or prerequisites are missing.
test-infrastructure-airflow: canonical-bootstrap
	SCENEOPS_TEST_AIRFLOW=1 EXPECTED_PIPELINE_BACKEND=airflow \
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) ENV_FILE=$(ENV_FILE) \
	$(REAL_INFRA_PYTEST) tests/infrastructure/test_airflow_backend.py -v

.PHONY: lint
lint:
	uv run ruff check apps/ packages/

.PHONY: format
format:
	uv run ruff format apps/ packages/
