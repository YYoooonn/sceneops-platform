# --------------------
# API
# --------------------

.PHONY: api-logs
api-logs:
	docker compose -f $(COMPOSE_FILE) logs -f api

.PHONY: api-shell
api-shell:
	docker compose -f $(COMPOSE_FILE) exec api sh

.PHONY: api-health
api-health:
	curl -sf $(API_HOST)/health | python3 -m json.tool

.PHONY: api-openapi
api-openapi:
	docker compose -f $(COMPOSE_FILE) exec api python -c \
		"from app.main import app; app.openapi(); print('api openapi ok')"
