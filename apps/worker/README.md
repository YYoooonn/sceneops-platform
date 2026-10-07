# sceneops-worker

Celery workers and the worker CLI. The worker executes durable Jobs and advances
durable Pipeline state; it never runs a Pipeline inline.

```text
sceneops_worker/
  tasks/        # Celery tasks: run_job_task (job queue), advance_pipeline_task (pipeline queue)
  jobs/         # JobRunner (sole entry point for a Job), handler registry and handlers
  pipelines/    # PipelineOrchestrator: one short, repeatable step over durable pipeline state
  execution/    # ExecutionDispatcher: the two messages a worker sends (dispatch a Job, advance a run)
  core/         # WorkerContext and dependency wiring
  runtime/      # async runtime helper for Celery's synchronous task entry points
  stores/       # persistence ports over sceneops-db
  scenes/ episodes/ recordings/ robots/   # canonical building, registration and recording resolution
  derived/      # content-pinned publication of derived artifacts (labels, views, manifests)
  runs/ inference/ evaluation/            # run artifacts, prediction and evaluation logic
  cli/          # `sceneops-worker jobs run`, `sceneops-worker pipelines advance`
```

See [`docs/architecture/jobs-and-pipelines.md`](../../docs/architecture/jobs-and-pipelines.md)
for the execution model.
