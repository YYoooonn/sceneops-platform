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

.PHONY: test
# All infrastructure-independent automated tests — no Postgres/MinIO/network/
# GPU/model weights required. apps/inference-server/tests is included: every
# test there mocks GroundingDinoModel/ImageResolver -- there is no runtime/
# model-dependent pytest suite; that path is only exercised by
# `make e2e-perception BACKEND=grounding_dino`. scripts/e2e/tests/
# test_e2e_fixture_bootstrap.py is the E2E fixture bootstrap's own unit
# suite — Postgres is faked in-memory there, so it belongs in the fast tier
# alongside everything else. packages/sceneops-streaming/tests is pure
# wire/schema unit tests (no Kafka broker) -- real-broker behavior is
# `make smoke-streaming`, not this tier.
test:
	uv run pytest apps/worker/tests/ apps/api/tests/ apps/inference-server/tests/ packages/sceneops-analytics/tests/ packages/sceneops-core/tests/ packages/sceneops-integrations/tests/ packages/sceneops-streaming/tests/ scripts/e2e/tests/test_e2e_fixture_bootstrap.py -v

.PHONY: test-integration
# Real-infrastructure tests. Prerequisite: `make local-up` (Postgres + MinIO
# + the `api` service reachable on their host-side local-stack ports). No
# separate pytest marker is used to select these — packages/sceneops-db/tests,
# packages/sceneops-storage/tests, and scripts/e2e/tests/
# test_e2e_fixture_bootstrap_integration.py/test_selective_reads_minio_
# integration.py/test_pipeline_contracts_integration.py are never included
# in `make test`'s testpaths, so directory/file separation alone is enough
# (see docs/development/local-development.md). test_e2e_fixture_bootstrap_
# integration.py additionally persists the shared E2E fixture catalog --
# E2E_BOOTSTRAP_SOURCE_ROOT_URI points its nuScenes-source check at the host
# filesystem path, mirroring `make e2e-bootstrap`'s own override.
# test_selective_reads_minio_integration.py needs only MinIO, not
# Postgres/nuScenes, but runs here for the same "real infra, not the fast
# tier" reason. test_pipeline_contracts_integration.py is the one test here
# that talks to the live `api` HTTP service rather than a direct DB/MinIO
# client -- `make local-up` already starts `api`, so this adds no new
# infrastructure requirement beyond what this target already documents.
test-integration:
	SCENEOPS_DATABASE_URL="postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$${POSTGRES_PORT:-5432}/$(POSTGRES_DB)" \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=$(MINIO_ROOT_USER) \
	MINIO_ROOT_PASSWORD=$(MINIO_ROOT_PASSWORD) \
	MINIO_BUCKET=$(MINIO_BUCKET) \
	E2E_BOOTSTRAP_SOURCE_ROOT_URI=$(CURDIR)/data/raw/nuscenes \
	API_BASE_URL=$(API_BASE_URL) API_PREFIX=$(API_PREFIX) \
	uv run pytest packages/sceneops-db/tests/ packages/sceneops-storage/tests/ scripts/e2e/tests/test_e2e_fixture_bootstrap_integration.py scripts/e2e/tests/test_selective_reads_minio_integration.py scripts/e2e/tests/test_pipeline_contracts_integration.py apps/worker/tests/robots/test_registration_integration.py -v

.PHONY: lint
lint:
	uv run ruff check apps/ packages/

.PHONY: format
format:
	uv run ruff format apps/ packages/
