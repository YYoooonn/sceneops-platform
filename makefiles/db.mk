# --------------------
# Database
# --------------------

.PHONY: migrate-build
migrate-build:
	uv lock
	docker compose -f $(COMPOSE_FILE) build migrate

.PHONY: db-migrate
db-migrate: migrate-build
	docker compose -f $(COMPOSE_FILE) up -d postgres
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate

.PHONY: db-revision
db-revision: migrate-build
	@if [ -z "$(MSG)" ]; then \
		echo "MSG is required. Usage: make db-revision MSG='create table'"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) revision --autogenerate -m "$(MSG)"

.PHONY: db-current
db-current:
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) current

.PHONY: db-history
db-history:
	docker compose -f $(COMPOSE_FILE) --profile tools run --rm migrate \
		alembic -c $(ALEMBIC_CONFIG) history

.PHONY: db-reset
db-reset:
	docker compose -f $(COMPOSE_FILE) down -v
	$(MAKE) db-migrate

.PHONY: db-shell
db-shell:
	docker compose -f $(COMPOSE_FILE) exec postgres psql -U sceneops -d sceneops
