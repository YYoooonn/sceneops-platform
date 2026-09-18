# --------------------
# API
# --------------------

.PHONY: api-logs
api-logs:
	$(COMPOSE) logs -f api

.PHONY: api-shell
api-shell:
	$(COMPOSE) exec api sh

.PHONY: api-health
api-health:
	curl -sf $(API_HOST)/health | python3 -m json.tool

.PHONY: api-openapi
api-openapi:
	$(COMPOSE) exec api python -c \
		"from app.main import app; app.openapi(); print('api openapi ok')"
