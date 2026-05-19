IMAGE_NAME=sceneops-platform
IMAGE_TAG=local

DATASET_ID := nuscenes-mini
DATASET_VERSION := v1.0-mini

NUSCENES_ROOT := /data/nuscenes
MANIFEST_ROOT := /data/manifests
ARTIFACT_ROOT := /data/artifacts

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

.PHONY: build-worker
build-worker:
	docker build \
		-f apps/worker/Dockerfile \
		-t $(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		.

.PHONY: run-worker
run-worker: build-worker
	docker run --rm \
		-e DATASET_ID=$(DATASET_ID) \
		-e DATASET_VERSION=$(DATASET_VERSION) \
		-e NUSCENES_ROOT=$(NUSCENES_ROOT) \
		-e MANIFEST_ROOT=$(MANIFEST_ROOT) \
		-e ARTIFACT_ROOT=$(ARTIFACT_ROOT) \
		-v $(PWD)/data/nuscenes:/data/nuscenes:ro \
		-v $(PWD)/data/manifests:/data/manifests \
		-v $(PWD)/data/artifacts:/data/artifacts \
		$(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		sceneops-worker ingest-nuscenes-mini --max-scenes $(MAX_SCENES)

.PHONY: ingest
ingest:
	@echo "INGESTING scene"
	docker run --rm \
		-e DATASET_ID=$(DATASET_ID) \
		-e DATASET_VERSION=$(DATASET_VERSION) \
		-e NUSCENES_ROOT=$(NUSCENES_ROOT) \
		-e MANIFEST_ROOT=$(MANIFEST_ROOT) \
		-e ARTIFACT_ROOT=$(ARTIFACT_ROOT) \
		-v $(PWD)/data/nuscenes:/data/nuscenes:ro \
		-v $(PWD)/data/manifests:/data/manifests \
		-v $(PWD)/data/artifacts:/data/artifacts \
		$(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		sceneops-worker ingest-nuscenes-mini --max-scenes $(MAX_SCENES)

.PHONY: clean-manifests
clean-manifests:
	rm -rf data/manifests/*
	mkdir -p data/manifests/scenes data/manifests/samples data/manifests/artifacts
