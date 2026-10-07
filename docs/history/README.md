# History

Point-in-time records: studies, benchmarks and acceptance reports kept as evidence of
what was examined or measured at a date and commit. **Nothing here describes the current
system.** A grep hit in this directory for a removed component (Airflow, the continuous
capture router, deleted `make` commands) is intentional; the current architecture is in
[`../architecture/`](../architecture/overview.md) and the reasons behind decisions are in
[`../adr/`](../adr/).

| Record | Subject | Current contract |
| --- | --- | --- |
| [learning-data-scaling-baseline.md](./learning-data-scaling-baseline.md) | audit, benchmarks and design record of the sharded learning-data layout | [scalable-learning-data.md](../architecture/scalable-learning-data.md) |
| [streaming-reliability-scale-baseline.md](./streaming-reliability-scale-baseline.md) | streaming failure-injection matrix and scale measurements | [streaming-transport.md](../architecture/streaming-transport.md) |
| [streaming-cleanroom-acceptance-phase6.7.md](./streaming-cleanroom-acceptance-phase6.7.md) | clean-room acceptance of the streaming pipeline at one commit | [streaming-transport.md](../architecture/streaming-transport.md), [test-matrix.md](../development/test-matrix.md) |
| [streaming-multirun-phase7-study.md](./streaming-multirun-phase7-study.md) | multi-run capture study; evaluated a router that was not adopted | [streaming-transport.md](../architecture/streaming-transport.md) §26, [ADR-008](../adr/008-acquisition-lifecycle-reliability.md) |
| [job-creation-concurrency-study.md](./job-creation-concurrency-study.md) | concurrent Job creation and claim experiments on real PostgreSQL | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5, [ADR-008](../adr/008-acquisition-lifecycle-reliability.md) |
| [job-state-transition-concurrency-study.md](./job-state-transition-concurrency-study.md) | stale-dispatch races on Job state transitions, real PostgreSQL | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5 |
| [job-worker-liveness-study.md](./job-worker-liveness-study.md) | worker loss, Celery redelivery and the ownership lease, real workers | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5, [ADR-010](../adr/010-job-ownership-lease-and-fencing.md) |

Old results are never rewritten when the implementation changes: a new report is added
instead. Implementation chronology (what changed and when) belongs to Git history.
