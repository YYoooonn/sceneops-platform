# Architecture

SceneOps is organized around a control plane / execution plane split.

```text
FastAPI API = control plane
Celery worker = execution plane
PostgreSQL = metadata source of truth
Local artifact store = artifact source of truth
Redis = async broker
```

---

## System overview

```text
[User / Script / Future Dashboard]
            |
            v
[FastAPI Control Plane]
            |
            v
[PostgreSQL Metadata]
            |
            v
[Celery Dispatch]
            |
            v
[Redis Broker]
            |
            v
[Worker Runtime]
            |
            v
[Job Handlers]
            |
            v
[Artifact Store]
```

---

## Main components

| Component | Responsibility |
|---|---|
| `apps/api` | FastAPI control plane for creating and reading datasets, models, jobs, pipelines, runs, artifacts |
| `apps/worker` | Celery worker and CLI runtime for executing jobs and pipelines |
| `sceneops-core` | Shared schemas, IDs, enums, pipeline definitions, contracts |
| `sceneops-db` | PostgreSQL models and repository implementations |
| `sceneops-storage` | Storage abstraction for artifact paths and file access |
| `migrations` | Alembic migration project |
| `scripts/e2e` | End-to-end validation scripts |

---

## Core domain model

```text
Dataset
  -> DatasetVersion
      -> DatasetManifest
      -> SceneManifest
      -> SampleManifest

Model
  -> ModelVersion
      -> backend
      -> model_uri / endpoint_url

PipelineRun
  -> PipelineStepRun
      -> Job
          -> JobEvent
          -> JobResult

InferenceRun
  -> PredictionManifest

EvaluationRun
  -> EvaluationManifest
  -> SampleEvaluationManifest
```

---

## Current built-in pipelines

### Dataset ingestion

```text
dataset_ingestion
  step 1: ingest_dataset
  step 2: validate_dataset_manifest
```

Purpose:

```text
raw nuScenes data
  -> dataset manifests
  -> validation
  -> ready dataset version
```

### Detection validation

```text
detection_validation
  step 1: predict_detection
  step 2: evaluate_detection
```

Purpose:

```text
ready dataset version + model version
  -> prediction artifacts
  -> evaluation artifacts
  -> model metrics
```

---

## Execution flow

```text
1. Client creates a pipeline run through FastAPI.
2. API stores PipelineRun and PipelineStepRun records in PostgreSQL.
3. API dispatches execution to Celery.
4. Celery worker loads the pipeline run.
5. PipelineRunner executes steps in dependency order.
6. Each step creates a Job record.
7. JobRunner executes the matching job handler.
8. Job handler reads/writes artifacts.
9. Job and pipeline metadata are updated in PostgreSQL.
10. Client polls pipeline/run/job APIs for result inspection.
```

---

## Design principles

| Principle | Meaning |
|---|---|
| Control plane / execution plane split | API creates and tracks work; worker executes work |
| Versioned inputs | DatasetVersion and ModelVersion are explicit inputs |
| Artifact-first outputs | Manifests and metrics are written as artifacts, not only DB rows |
| Metadata source of truth | PostgreSQL tracks status, lineage, and queryable records |
| Reproducible pipelines | Pipeline runs preserve type, params, steps, job IDs, and outputs |
| Backend abstraction | Inference backend can be mock, ONNX Runtime, or future serving backend |

---

## Current limitations

- Local filesystem is the only implemented artifact backend.
- Dataset profiling is not implemented yet.
- Official nuScenes metrics are not implemented yet.
- Model comparison / leaderboard APIs are not implemented yet.
- Prometheus/Grafana observability is not implemented yet.
- Simulation/counterfactual dataset contracts are still design targets.

---

## Architecture direction

The target architecture is not to become a full Kubeflow clone. The goal is to build a focused robotics perception workflow platform:

```text
sensor dataset management
  + model runtime validation
  + evaluation tracking
  + artifact lineage
  + async execution
```

This scope is intentionally practical for a portfolio-scale project while still demonstrating production-oriented AI system design.
