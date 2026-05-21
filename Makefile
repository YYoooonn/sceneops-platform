IMAGE_NAME=sceneops-platform
IMAGE_TAG=local

ENV_FILE=.env

DOCKER_VOLUMES = \
	-v $(PWD)/data/raw:/data/raw:ro \
	-v $(PWD)/data/manifests:/data/manifests \
	-v $(PWD)/data/artifacts:/data/artifacts \
	-v $(PWD)/data/runs:/data/runs \

DATASET_ID ?= nuscenes
DATASET_VERSION ?= v1.0-mini

MAX_SCENES ?= 2
INGEST_MODE ?= upsert

MODEL_ID ?= centerpoint-mock
MODEL_VERSION ?= v0
RUN_ID ?= run-centerpoint-mock-001
EVALUATION_RUN_ID ?= eval-centerpoint-mock-001
MAX_SAMPLES ?= 20
MATCH_DISTANCE_M ?= 2.0

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

.PHONY: worker-ingest
worker-ingest:
	@echo "INGESTING scene"
	docker run --rm \
		--env-file $(ENV_FILE) \
		$(DOCKER_VOLUMES) \
		$(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		sceneops-worker ingest nuscenes \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--max-scenes $(MAX_SCENES) \
			--mode $(INGEST_MODE)

.PHONY: worker-predict-mock-detection
worker-predict-mock-detection:
	@echo "mock detection"
	docker run --rm \
		--env-file $(ENV_FILE) \
		$(DOCKER_VOLUMES) \
		$(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		sceneops-worker predict mock-detection \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--model-id $(MODEL_ID) \
			--model-version $(MODEL_VERSION) \
			--run-id $(RUN_ID) \
			--max-samples $(MAX_SAMPLES)

.PHONY: worker-evaluate-detection
worker-evaluate-detection:
	@echo "evaluate detection"
	docker run --rm \
		--env-file $(ENV_FILE) \
		$(DOCKER_VOLUMES) \
		$(IMAGE_NAME)/worker:$(IMAGE_TAG) \
		sceneops-worker evaluate detection \
			--dataset-id $(DATASET_ID) \
			--dataset-version $(DATASET_VERSION) \
			--inference-run-id $(RUN_ID) \
			--evaluation-run-id $(EVALUATION_RUN_ID) \
			--match-distance-m $(MATCH_DISTANCE_M)


.PHONY: prepare-data
prepare-data:
	mkdir -p data/manifests data/artifacts data/runs

.PHONY: clean-all
clean-all: rm -rf data/manifests/* \
	rm -rf data/artifacts \
	rm -rf data/runs \
	prepare-data


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
		$(DOCKER_VOLUMES) \
		$(API_FULL_IMAGE)
