# --------------------
# E2E
# --------------------

.PHONY: e2e-api-smoke
e2e-api-smoke:
	chmod +x scripts/e2e/e2e_api_smoke.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_api_smoke.sh

.PHONY: e2e-dataset-ingestion
e2e-dataset-ingestion:
	chmod +x scripts/e2e/e2e_dataset_scene_ingestion.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_dataset_scene_ingestion.sh

.PHONY: e2e-raw-log-scene-building
e2e-raw-log-scene-building:
	chmod +x scripts/e2e/e2e_raw_log_scene_building.sh
	API_PREFIX=$(API_PREFIX) scripts/e2e/e2e_raw_log_scene_building.sh

.PHONY: e2e-detection-evaluation
e2e-detection-evaluation:
	chmod +x scripts/e2e/e2e_detection_evaluation.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	MODEL_ID=$(MODEL_ID) MODEL_VERSION=$(MODEL_VERSION) \
	scripts/e2e/e2e_detection_evaluation.sh

.PHONY: e2e-pipeline-contracts
e2e-pipeline-contracts:
	chmod +x scripts/e2e/e2e_pipeline_contracts.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_pipeline_contracts.sh

.PHONY: e2e-detection-evaluation-real
e2e-detection-evaluation-real: e2e-detection-evaluation-groundingdino

.PHONY: e2e-detection-evaluation-groundingdino
e2e-detection-evaluation-groundingdino:
	chmod +x scripts/e2e/e2e_detection_evaluation_groundingdino.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	MODEL_ID=$(GDINO_MODEL_ID) MODEL_VERSION=$(GDINO_MODEL_VERSION) \
	INFERENCE_ENDPOINT_URL=$(INFERENCE_ENDPOINT_URL) \
	SCENARIO_SET_ID=$(SCENARIO_SET_ID) \
	SCENARIO_CURATION_PIPELINE_RUN_ID=$(SCENARIO_CURATION_PIPELINE_RUN_ID) \
	PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) \
	scripts/e2e/e2e_detection_evaluation_groundingdino.sh

.PHONY: e2e-scenario-curation
e2e-scenario-curation:
	chmod +x scripts/e2e/e2e_scenario_curation.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_scenario_curation.sh

.PHONY: e2e-analytics-export
e2e-analytics-export:
	chmod +x scripts/e2e/e2e_analytics_export.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_analytics_export.sh

.PHONY: e2e-reliability
e2e-reliability:
	chmod +x scripts/e2e/e2e_reliability.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_reliability.sh

.PHONY: e2e-airflow-pipeline
e2e-airflow-pipeline:
	chmod +x scripts/e2e/e2e_airflow_pipeline.sh
	API_PREFIX=$(API_PREFIX) \
	DATASET_ID=$(DATASET_ID) DATASET_VERSION=$(DATASET_VERSION) \
	scripts/e2e/e2e_airflow_pipeline.sh

.PHONY: e2e
e2e: e2e-api-smoke e2e-dataset-ingestion e2e-detection-evaluation e2e-pipeline-contracts
