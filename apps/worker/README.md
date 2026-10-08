# sceneops-worker

Celery workers and the worker CLI. The worker executes durable Jobs and advances
durable Pipeline state; it never runs a Pipeline inline.

```text
sceneops_worker/
  celery_app.py # the Celery app, its worker lifecycle hooks, the execution dispatcher over it
  tasks/        # Celery tasks: run_job_task (job queue), advance_pipeline_task (pipeline queue)
  jobs/         # JobRunner (sole entry point for a Job), handler registry and handlers
  pipelines/    # PipelineOrchestrator: one short, repeatable step over durable pipeline state
  core/         # WorkerContext and dependency wiring
  stores/       # persistence ports over sceneops-db
  runtime/      # async runtime helper for Celery's synchronous task entry points
  scenes/ episodes/ robots/ recordings/ derived/ inference/
                # registration, resolution and publication use cases that run on a WorkerContext
  cli/          # `sceneops-worker jobs | pipelines | recover | execution-status | acquisition ...`
```

What is not here: lease and recovery mechanics, Job / Pipeline services
(`sceneops-execution`), acquisition reconciliation (`sceneops-acquisition`), and the
Scene, Episode, derived-storage, inference and evaluation logic the handlers call
(`sceneops-scenes`, `-episodes`, `-derived`, `-inference`, `-evaluation`). The worker keeps
what takes a `WorkerContext`.

See [`docs/architecture/repository-structure.md`](../../docs/architecture/repository-structure.md)
for the boundaries and [`docs/architecture/jobs-and-pipelines.md`](../../docs/architecture/jobs-and-pipelines.md)
for the execution model.
