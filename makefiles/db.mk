# --------------------
# Database
# --------------------

.PHONY: migrate-build
migrate-build:
	uv lock
	$(COMPOSE) build migrate

.PHONY: db-migrate
# Does not depend on migrate-build — compose builds the `migrate` image
# on first run and reuses it after, so repeated `make local-up` calls
# don't pay a rebuild + `uv lock` cost every time. Run `make migrate-build`
# explicitly after changing migration dependencies.
db-migrate:
	$(COMPOSE) up -d --wait postgres
	$(COMPOSE) --profile tools run --rm migrate

.PHONY: db-revision
db-revision: migrate-build
	@if [ -z "$(MSG)" ]; then \
		echo "MSG is required. Usage: make db-revision MSG='create table'"; \
		exit 1; \
	fi
	$(COMPOSE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) revision --autogenerate -m "$(MSG)"

.PHONY: db-current
db-current:
	$(COMPOSE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) current

.PHONY: db-history
db-history:
	$(COMPOSE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) history

.PHONY: db-reset
# Scoped to Postgres only — does NOT touch Redis/MinIO data (unlike a bare
# `compose down -v`, which would since minio is a default, non-profiled
# service now). Use `make local-reset` to wipe the whole local stack.
db-reset:
	$(COMPOSE) stop postgres
	$(COMPOSE) rm -f postgres
	docker volume rm -f $$(docker volume ls -q --filter label=com.docker.compose.project=sceneops --filter label=com.docker.compose.volume=postgres-data)
	$(MAKE) db-migrate

.PHONY: db-shell
db-shell:
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)
