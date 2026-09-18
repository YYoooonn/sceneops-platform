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
# test there mocks GroundingDinoModel/ImageResolver (confirmed during
# Stabilization Request 4's audit — there is currently no runtime/model-
# dependent pytest suite; that path is exercised only by
# e2e-detection-evaluation-groundingdino).
test:
	uv run pytest apps/worker/tests/ apps/api/tests/ apps/inference-server/tests/ packages/sceneops-analytics/tests/ packages/sceneops-core/tests/ -v

.PHONY: test-integration
# Real-infrastructure tests. Prerequisite: `make local-up` (Postgres + MinIO
# reachable on their host-side local-stack ports). No separate pytest marker
# is used to select these — packages/sceneops-db/tests and
# packages/sceneops-storage/tests are dedicated directories never included
# in `make test`'s testpaths, so directory separation alone is enough (see
# docs/development/local-development.md).
test-integration:
	SCENEOPS_DATABASE_URL="postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@localhost:$${POSTGRES_PORT:-5432}/$(POSTGRES_DB)" \
	MINIO_ENDPOINT_URL="http://localhost:$${MINIO_API_PORT:-9000}" \
	MINIO_ROOT_USER=minioadmin \
	MINIO_ROOT_PASSWORD=minioadmin \
	MINIO_BUCKET=sceneops \
	uv run pytest packages/sceneops-db/tests/ packages/sceneops-storage/tests/ -v

.PHONY: lint
lint:
	uv run ruff check apps/ packages/

.PHONY: format
format:
	uv run ruff format apps/ packages/
