# Operations

SceneOps is designed as a production-like local system. The current implementation already separates API, metadata, broker, and worker services.

---

## Current local services

| Service | Role |
|---|---|
| PostgreSQL | Metadata database |
| Redis | Celery broker |
| FastAPI API | Control plane |
| Celery worker | Async execution runtime |
| Worker CLI | Debug and manual execution |
| Migration container | Alembic migration runner |

Start services:

```bash
make compose-up
```

Stop services:

```bash
make compose-down
```

Reset local state:

```bash
make reset-local
```

---

## Current checks

```bash
make check-env
make check-imports
make check-celery
```

These verify:

- expected local environment variables/directories
- Python package import health
- Celery broker connectivity

---

## Current debug commands

```bash
make show-runs
make show-pipeline PIPELINE_RUN_ID=pipe-xxx
make show-job-events JOB_ID=job-xxx
```

These commands are useful for debugging:

- pipeline status
- pipeline step status
- job status
- job event timeline
- inference/evaluation outputs

---

## Current operational signals

| Signal | Current status |
|---|---|
| API health endpoint | implemented |
| job status | implemented |
| job events | implemented |
| pipeline status | implemented |
| pipeline step status | implemented |
| inference run status | implemented |
| evaluation run status | implemented |
| worker logs | available through Docker Compose |
| queue latency metric | target |
| pipeline duration metric | target |
| Prometheus endpoint | target |
| Grafana dashboard | target |

---

## Recommended next endpoint: pipeline timeline

Target:

```text
GET /api/v1/pipelines/runs/{pipeline_run_id}/timeline
```

Target response:

```json
{
  "pipeline_run_id": "pipe_xxx",
  "status": "succeeded",
  "events": [
    {
      "time": "2026-05-31T10:00:00Z",
      "type": "pipeline_created"
    },
    {
      "time": "2026-05-31T10:00:02Z",
      "type": "step_started",
      "step": "predict",
      "job_id": "job_xxx"
    },
    {
      "time": "2026-05-31T10:00:05Z",
      "type": "step_succeeded",
      "step": "predict",
      "job_id": "job_xxx"
    },
    {
      "time": "2026-05-31T10:00:07Z",
      "type": "pipeline_succeeded"
    }
  ]
}
```

Why this matters:

- easier debugging of blocked/failed pipelines
- clearer portfolio story around operational visibility
- direct alignment with monitoring and failure-response requirements

---

## Recommended next metrics

Target:

```text
GET /api/v1/metrics/jobs
GET /api/v1/metrics/pipelines
```

Initial metrics:

```json
{
  "job_counts": {
    "pending": 0,
    "running": 1,
    "succeeded": 12,
    "failed": 2
  },
  "pipeline_counts": {
    "pending": 0,
    "running": 0,
    "succeeded": 5,
    "failed": 1
  },
  "recent_failures": [
    {
      "id": "job_xxx",
      "type": "validate_dataset_manifest",
      "error_type": "ValueError",
      "message": "Dataset manifest validation failed"
    }
  ]
}
```

---

## Production direction

Future production-like operations should add:

- structured JSON logs
- Prometheus metrics endpoint
- Grafana dashboard
- retry policy visualization
- queue latency tracking
- inference latency tracking
- failed validation report inspection
- worker heartbeat monitoring
- storage backend health checks
