# --------------------
# Debug
# --------------------

.PHONY: show-runs
show-runs:
	@echo "== Inference runs =="
	@curl -sS "$(API_HOST)$(API_PREFIX)/inference/runs" | python3 -m json.tool || true
	@echo ""
	@echo "== Evaluation runs =="
	@curl -sS "$(API_HOST)$(API_PREFIX)/evaluations/runs" | python3 -m json.tool || true

.PHONY: show-pipeline
show-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make show-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	curl -sS "$(API_HOST)$(API_PREFIX)/pipelines/runs/$(PIPELINE_RUN_ID)" | python3 -m json.tool

.PHONY: show-job-events
show-job-events:
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make show-job-events JOB_ID=job-xxx"; \
		exit 1; \
	fi
	curl -sS "$(API_HOST)$(API_PREFIX)/jobs/$(JOB_ID)/events" | python3 -m json.tool

.PHONY: compare-detection
compare-detection:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make show-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/compare_detection_run.sh
	API_PREFIX=$(API_PREFIX) PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) scripts/debug/compare_detection_run.sh
