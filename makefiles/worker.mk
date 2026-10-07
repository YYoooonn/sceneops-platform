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

.PHONY: worker-run-pipeline
worker-run-pipeline:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	$(COMPOSE) --profile debug run --rm worker-cli \
		sceneops-worker pipelines run --pipeline-run-id $(PIPELINE_RUN_ID)


.PHONY: worker-run-pipeline-task
worker-run-pipeline-task:
	@if [ -z "$(PIPELINE_RUN_ID)" ]; then \
		echo "PIPELINE_RUN_ID is required. Usage: make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx"; \
		exit 1; \
	fi
	$(COMPOSE) --profile debug run --rm worker-cli \
		sceneops-worker run-pipeline-task --pipeline-run-id $(PIPELINE_RUN_ID) --task-id $(TASK_ID)

.PHONY: worker-register-robot-run
# REGISTER_ROBOT_RUN for an already-published RobotRunManifest (same
# registrar as POST /robot-runs:register). Publish first with
# `python -m sceneops_integrations.recording publish`.
worker-register-robot-run:
	@if [ -z "$(MANIFEST_URI)" ]; then \
		echo "Usage: make worker-register-robot-run MANIFEST_URI=s3://sceneops/artifacts/robot_runs/run-1/robot_run_manifest.json"; \
		exit 1; \
	fi
	$(COMPOSE) --profile debug run --rm worker-cli \
		sceneops-worker robots register --manifest-uri $(MANIFEST_URI)
