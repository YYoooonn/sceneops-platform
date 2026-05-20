IMAGE_NAME=sceneops-platform
IMAGE_TAG=local

ENV_FILE=.env

MAX_SCENES ?= 2

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "help"
	@echo ""

# lint and format
.PHONY: install-dev
install-dev:
	python -m pip install -r requirements-dev.txt
	pre-commit install

.PHONY: check
check:
	pre-commit run --all-files

.PHONY: uninstall-hooks
uninstall-hooks:
	pre-commit uninstall

# --------------------
# -------WORKER-------
# --------------------

.PHONY: build-worker
build-worker:
	docker build \
		-f apps/worker/Dockerfile \
		-t $(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		.

.PHONY: run-worker
run-worker: build-worker
	docker run --rm \
		--env-file $(ENV_FILE) \
		-v $(PWD)/data/raw:/data/raw:ro \
		-v $(PWD)/data/manifests:/data/manifests \
		-v $(PWD)/data/artifacts:/data/artifacts \
		$(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		sceneops-worker ingest-nuscenes \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--max-scenes $(MAX_SCENES)

.PHONY: ingest
ingest:
	@echo "INGESTING scene"
	docker run --rm \
		--env-file $(ENV_FILE) \
		-v $(PWD)/data/raw:/data/raw:ro \
		-v $(PWD)/data/manifests:/data/manifests \
		-v $(PWD)/data/artifacts:/data/artifacts \
		$(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		sceneops-worker ingest-nuscenes \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--max-scenes $(MAX_SCENES)

.PHONY: clean-manifests
clean-manifests:
	rm -rf data/manifests/*
	mkdir -p data/manifests/datasets


# --------------------
# --------API---------
# --------------------
API_FULL_IMAGE := $(IMAGE_NAME)/api:$(IMAGE_TAG)

.PHONY: build-api
build-api:
	docker build \
		-f apps/api/Dockerfile \
		-t $(API_FULL_IMAGE) \
		.

.PHONY: run-api
run-api: build-api
	docker run --rm \
		--env-file $(ENV_FILE) \
		-p 8000:8000 \
		-v $(PWD)/data/raw:/data/raw:ro \
		-v $(PWD)/data/manifests:/data/manifests:ro \
		-v $(PWD)/data/artifacts:/data/artifacts:ro \
		$(API_FULL_IMAGE)
