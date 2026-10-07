# --------------------
# Worker
# --------------------

.PHONY: worker-logs
worker-logs:
	$(COMPOSE) logs -f worker-pipeline worker-jobs

.PHONY: worker-shell
worker-shell:
	$(COMPOSE) --profile debug run --rm --entrypoint sh worker-cli

.PHONY: worker-python
worker-python:
	$(COMPOSE) --profile debug run --rm --entrypoint python worker-cli

.PHONY: worker-cli
worker-cli:
	$(COMPOSE) --profile debug run --rm worker-cli

.PHONY: worker-run-job
worker-run-job:
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make worker-run-job JOB_ID=job-xxx"; \
		exit 1; \
	fi
	$(COMPOSE) --profile debug run --rm worker-cli \
		sceneops-worker jobs run --job-id $(JOB_ID)

.PHONY: worker-advance-pipeline
# One orchestration step of a QUEUED or RUNNING pipeline run (the step a pipeline
# worker takes when a Job reports): settles a finished Job, submits the next one.
worker-advance-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-advance-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	$(COMPOSE) --profile debug run --rm worker-cli \
		sceneops-worker pipelines advance --pipeline-run-id $(PIPELINE_RUN_ID)
