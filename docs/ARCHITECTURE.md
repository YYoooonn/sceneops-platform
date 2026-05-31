# Architecture

## Overview

SceneOps Platform is a local-first MLOps platform for robotics perception workflows.

The architecture separates control, execution, metadata, artifact, and model-runtime concerns.

```text
[Client / CLI / E2E Script / Dashboard]
                    |
                    v
[FastAPI Control Plane]
                    |
                    v
[PostgreSQL Metadata Store]
                    |
                    v
[Execution Dispatcher]
                    |
                    v
[Redis Broker]
                    |
                    v
[Celery Worker Runtime]
                    |
                    v
[Job / Pipeline Executors]
                    |
                    v
[Artifact Store + Model Runtime]
```

## Core responsibilities

### API control plane

The API owns user-facing operations:

- register datasets and dataset versions
- register models and model versions
- create pipeline runs
- dispatch pipeline/job execution
- inspect jobs and job events
- inspect inference/evaluation runs
- expose artifact metadata

The API should not directly execute heavy data/model work. It creates metadata records and dispatches execution.

### Metadata store

PostgreSQL stores durable execution and registry metadata:

```text
datasets
dataset_versions
models
model_versions
jobs
job_events
pipeline_runs
pipeline_step_runs
inference_runs
evaluation_runs
```

The database stores metadata, not large artifacts.

### Execution dispatcher

The execution dispatcher abstracts how work is executed.

Current backend:

```text
CeleryExecutionDispatcher
```

Future backend candidates:

```text
AirflowExecutionDispatcher
KubernetesJobExecutionDispatcher
KubeflowExecutionDispatcher
ArgoExecutionDispatcher
```

The API should depend on the dispatcher interface, not on Celery directly.

### Redis/Celery execution layer

Redis is used as the local broker.

Celery workers listen to execution queues:

```text
sceneops.pipeline_runs
sceneops.jobs
```

The worker receives either a pipeline run id or a job id and delegates execution to runtime classes.

### Worker runtime

The worker runtime contains orchestration logic:

```text
PipelineRuntime
JobRuntime
PipelineRunner
JobRunner
```

The runtime is responsible for:

- loading records from the repository
- validating runnable states
- transitioning statuses
- executing steps/jobs
- writing job events
- writing run results
- handling failures and retries
- propagating outputs between pipeline steps

### Executors

Executors perform concrete work:

```text
dataset ingestion
dataset validation
dataset profiling
prediction
evaluation
```

Executors should be small, replaceable, and bound to typed input/output contracts.

### Artifact store

Artifacts are large outputs produced by workflows:

```text
dataset manifests
validation reports
profile reports
prediction manifests
evaluation reports
model artifacts
```

Current mode:

```text
local filesystem under /data
```

Target mode:

```text
local://
s3://
minio://
```

The platform should store artifact URI and metadata in PostgreSQL while keeping actual files in the artifact store.

## Data flow

### Dataset ingestion flow

```text
POST /datasets
POST /datasets/{dataset_id}/versions
POST /pipelines/runs
POST /pipelines/runs/{pipeline_run_id}/execute
        |
        v
Celery task
        |
        v
PipelineRunner
        |
        v
IngestDatasetJob
        |
        v
dataset manifest artifact
        |
        v
dataset version metadata update
```

### Detection validation flow

```text
register model
register model version
create detection_validation pipeline run
dispatch pipeline run
        |
        v
predict_detection job
        |
        v
prediction manifest
        |
        v
evaluate_detection job
        |
        v
evaluation manifest + metrics
```

## Pipeline model

A pipeline run is a workflow-level execution record.

A pipeline run owns multiple pipeline step runs.

Each pipeline step may create and execute a job.

```text
PipelineRun
  ├── PipelineStepRun(dataset_ingest)
  │     └── Job(ingest_dataset)
  ├── PipelineStepRun(predict)
  │     └── Job(predict_detection)
  └── PipelineStepRun(evaluate)
        └── Job(evaluate_detection)
```

## Job lifecycle

Target lifecycle:

```text
pending -> queued -> running -> succeeded
pending -> queued -> running -> failed
```

Job events should capture the execution timeline:

```text
queued
locked
heartbeat
started
step_started
step_succeeded
step_failed
succeeded
failed
```

## Final architecture target

```text
[Dashboard]
    |
[FastAPI API]
    |
[PostgreSQL Metadata]
    |
[Execution Dispatcher]
    |----------------------|
    |                      |
[Celery/Redis]       [Airflow/K8s later]
    |
[Worker Runtime]
    |
[Executors]
    |
    |----------|-----------|-------------|
    v          v           v             v
Dataset     Model       Evaluation    Artifact
Registry    Runtime     Engine        Store
```

## Design rule

The architecture should keep the following boundaries strict:

```text
API creates and dispatches work.
Worker executes work.
Database stores metadata.
Artifact store stores large outputs.
Model runtime performs inference.
Evaluation engine produces metrics.
```
