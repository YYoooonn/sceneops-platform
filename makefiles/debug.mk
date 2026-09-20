# --------------------
# Debug
# --------------------

.PHONY: show-runs
show-runs:
	chmod +x scripts/debug/show_runs.sh
	API_PREFIX=$(API_PREFIX) scripts/debug/show_runs.sh

.PHONY: show-pipeline
show-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make show-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/show_pipeline.sh
	API_PREFIX=$(API_PREFIX) PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) scripts/debug/show_pipeline.sh

.PHONY: show-job-events
show-job-events:
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make show-job-events JOB_ID=job-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/show_job_events.sh
	API_PREFIX=$(API_PREFIX) JOB_ID=$(JOB_ID) scripts/debug/show_job_events.sh

.PHONY: tail-worker-logs
tail-worker-logs:
	chmod +x scripts/debug/tail_worker_logs.sh
	scripts/debug/tail_worker_logs.sh

.PHONY: compare-detection
compare-detection:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make show-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	chmod +x scripts/debug/compare_detection_run.sh
	API_PREFIX=$(API_PREFIX) PIPELINE_RUN_ID=$(PIPELINE_RUN_ID) scripts/debug/compare_detection_run.sh
