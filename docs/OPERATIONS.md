# Operations

This document describes local operations, debugging, and future monitoring goals.

## Local services

The local compose stack contains:

```text
postgres
redis
api
worker-celery
worker-cli
migrate
```

Start core services:

```bash
make compose-up
```

Build images:

```bash
make compose-build
```

Stop services:

```bash
make compose-down
```

Reset volumes:

```bash
make compose-down-volumes
```

## Database operations

Run migrations:

```bash
make db-migrate
```

Create migration:

```bash
make db-revision MSG="add table"
```

Show current migration:

```bash
make db-current
```

Show history:

```bash
make db-history
```

Reset database:

```bash
make db-reset
```

## Worker operations

Run Celery worker through compose:

```bash
make compose-up
```

View worker logs:

```bash
make worker-logs
```

Open worker shell:

```bash
make worker-shell
```

Run a single job manually:

```bash
make worker-run-job JOB_ID=job-xxx
```

Run a single pipeline manually:

```bash
make worker-run-pipeline PIPELINE_RUN_ID=pipe-xxx
```

## API operations

View API logs:

```bash
make api-logs
```

Open API shell:

```bash
make api-shell
```

## Health checks

```bash
make check-env
make check-imports
make check-celery
```

## Debugging

```bash
make show-runs
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
make show-job-events JOB_ID=job-xxx
```

## Operational metrics target

The platform should eventually expose metrics for:

```text
pipeline count
pipeline duration
pipeline failure count
job count
job duration
job queue latency
job retry count
job failure count
inference latency
evaluation duration
dataset validation failure count
artifact write failure count
```

Prometheus metric names:

```text
sceneops_pipeline_total
sceneops_pipeline_duration_seconds
sceneops_pipeline_failures_total
sceneops_job_total
sceneops_job_duration_seconds
sceneops_job_queue_latency_seconds
sceneops_job_retries_total
sceneops_job_failures_total
sceneops_inference_latency_seconds
sceneops_evaluation_duration_seconds
sceneops_dataset_validation_failures_total
```

## Failure handling target

The worker should support:

```text
retryable errors
non-retryable errors
stale running job recovery
heartbeat timeout detection
worker id tracking
failure reason persistence
event timeline inspection
```

## Monitoring roadmap

### Step 1. Structured logs

Add consistent structured logs with:

```text
pipeline_run_id
job_id
worker_id
step_name
status
duration_ms
error_type
error_message
```

### Step 2. Metrics endpoint

Expose summary metrics from API:

```text
GET /api/v1/metrics/pipelines
GET /api/v1/metrics/jobs
GET /api/v1/metrics/datasets/{dataset_id}/versions/{version}
```

### Step 3. Prometheus

Expose Prometheus metrics endpoint:

```text
GET /metrics
```

### Step 4. Grafana

Add local Grafana dashboard for:

```text
pipeline health
job health
queue latency
inference latency
evaluation duration
failure trends
```

## Production-oriented target

The local-first system should be able to evolve into:

```text
FastAPI API
PostgreSQL
Redis/Celery or Kubernetes jobs
MinIO/S3 artifact store
Triton model serving
Prometheus/Grafana monitoring
MLflow model registry
```
