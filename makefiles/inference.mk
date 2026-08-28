# --------------------
# Inference (local CPU)
# --------------------

.PHONY: inference-local-build
inference-local-build:
	docker compose -f $(COMPOSE_FILE) --profile inference build inference-server-local

.PHONY: inference-local-up
inference-local-up:
	mkdir -p cache/hf
	docker compose -f $(COMPOSE_FILE) --profile inference up -d inference-server-local

.PHONY: inference-local-down
inference-local-down:
	docker compose -f $(COMPOSE_FILE) --profile inference stop inference-server-local
	docker compose -f $(COMPOSE_FILE) --profile inference rm -f inference-server-local

.PHONY: inference-local-logs
inference-local-logs:
	docker compose -f $(COMPOSE_FILE) --profile inference logs -f inference-server-local

.PHONY: check-inference-server
check-inference-server:
	curl -sf http://localhost:8001/healthz | python3 -m json.tool

.PHONY: check-inference-server-ready
check-inference-server-ready:
	curl -sf http://localhost:8001/readyz | python3 -m json.tool

# --------------------
# Inference (GPU)
# --------------------

.PHONY: inference-gpu-build
inference-gpu-build:
	docker compose -f $(COMPOSE_FILE) --profile gpu build inference-server

.PHONY: inference-gpu-up
inference-gpu-up:
	mkdir -p cache/hf
	docker compose -f $(COMPOSE_FILE) --profile gpu up -d inference-server

.PHONY: inference-gpu-down
inference-gpu-down:
	docker compose -f $(COMPOSE_FILE) --profile gpu stop inference-server
	docker compose -f $(COMPOSE_FILE) --profile gpu rm -f inference-server

.PHONY: inference-gpu-logs
inference-gpu-logs:
	docker compose -f $(COMPOSE_FILE) --profile gpu logs -f inference-server
