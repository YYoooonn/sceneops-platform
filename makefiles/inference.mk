# --------------------
# Inference (local CPU)
# --------------------

.PHONY: inference-local-build
inference-local-build:
	$(COMPOSE) --profile inference build inference-server-local

.PHONY: inference-local-up
inference-local-up:
	mkdir -p cache/hf
	$(COMPOSE) --profile inference up -d inference-server-local

.PHONY: inference-local-down
inference-local-down:
	$(COMPOSE) --profile inference stop inference-server-local
	$(COMPOSE) --profile inference rm -f inference-server-local

.PHONY: inference-local-logs
inference-local-logs:
	$(COMPOSE) --profile inference logs -f inference-server-local

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
	$(COMPOSE) --profile gpu build inference-server-gpu

.PHONY: inference-gpu-up
inference-gpu-up:
	mkdir -p cache/hf
	$(COMPOSE) --profile gpu up -d inference-server-gpu

.PHONY: inference-gpu-down
inference-gpu-down:
	$(COMPOSE) --profile gpu stop inference-server-gpu
	$(COMPOSE) --profile gpu rm -f inference-server-gpu

.PHONY: inference-gpu-logs
inference-gpu-logs:
	$(COMPOSE) --profile gpu logs -f inference-server-gpu
