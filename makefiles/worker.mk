# --------------------
# Worker
# --------------------

.PHONY: worker-logs
worker-logs:
	docker compose -f $(COMPOSE_FILE) logs -f worker-pipeline worker-jobs

.PHONY: worker-shell
worker-shell:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm --entrypoint sh worker-cli

.PHONY: worker-python
worker-python:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm --entrypoint python worker-cli

.PHONY: worker-imports
worker-imports:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm --entrypoint python worker-cli \
		-c "import sceneops_worker; from sceneops_worker.jobs.registry import create_default_job_handler_registry; print('worker import ok'); print(create_default_job_handler_registry())"

.PHONY: worker-cli
worker-cli:
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli

.PHONY: worker-run-job
worker-run-job:
	@if [ -z "$(JOB_ID)" ]; then \
		echo "JOB_ID is required. Usage: make worker-run-job JOB_ID=job-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli \
		sceneops-worker jobs run --job-id $(JOB_ID)

.PHONY: worker-run-pipeline
worker-run-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli \
		sceneops-worker pipelines run --pipeline-run-id $(PIPELINE_RUN_ID)


.PHONY: worker-run-pipeline-task
worker-run-pipeline-task:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	docker compose -f $(COMPOSE_FILE) --profile debug run --rm worker-cli \
		sceneops-worker run-pipeline-task --pipeline-run-id $(PIPELINE_RUN_ID) --task-id $(TASK_ID)
