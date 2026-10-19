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
| [streaming-consumer-group-study.md](./streaming-consumer-group-study.md) | whether consumer-group state (the commit after finalize) is needed by a pinned Capture: recovery, group loss, lag, accumulation | [streaming-transport.md](../architecture/streaming-transport.md) §21, §26, [ADR-013](../adr/013-capture-records-no-kafka-progress.md) |
| [streaming-retention-study.md](./streaming-retention-study.md) | Kafka replay window: retention timing, data rates and disk budget of a sensor workload, retention failure experiments for a pinned Capture | [streaming-transport.md](../architecture/streaming-transport.md) §26.1, [ADR-014](../adr/014-kafka-replay-window.md) |
| [streaming-backpressure-study.md](./streaming-backpressure-study.md) | RobotRun arrival vs Capture service: concurrent Captures, backlog regimes, replay headroom and expiry under overload, producer under a broker stall | [streaming-transport.md](../architecture/streaming-transport.md) §14, §26.1, [ADR-014](../adr/014-kafka-replay-window.md) |
| [streaming-producer-overload-study.md](./streaming-producer-overload-study.md) | producer queue saturation: why a full librdkafka queue never drained, recovery strategies, bounded wait, the bridge's exit status, outage runs through bridge and Capture | [streaming-transport.md](../architecture/streaming-transport.md) §4, §14 |
| [streaming-producer-semantics-study.md](./streaming-producer-semantics-study.md) | producer ordering / duplicate behaviour under retries and faults; partition-count metadata refresh | [streaming-transport.md](../architecture/streaming-transport.md) §4, §14 |
| [job-creation-concurrency-study.md](./job-creation-concurrency-study.md) | concurrent Job creation and claim experiments on real PostgreSQL | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5, [ADR-008](../adr/008-acquisition-lifecycle-reliability.md) |
| [job-state-transition-concurrency-study.md](./job-state-transition-concurrency-study.md) | stale-dispatch races on Job state transitions, real PostgreSQL | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5 |
| [job-worker-liveness-study.md](./job-worker-liveness-study.md) | worker loss, Celery redelivery and the ownership lease, real workers | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5, [ADR-010](../adr/010-job-ownership-lease-and-fencing.md) |
| [lost-dispatch-study.md](./lost-dispatch-study.md) | database / broker dual write: lost `run_job` and `advance` messages, state-derived redispatch | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5, [ADR-011](../adr/011-state-derived-redispatch.md) |
| [multi-source-architecture-audit.md](./multi-source-architecture-audit.md) | RobotRun coupling, DatasetVersion mutability and duplicated temporal association, audited for the multi-source target | [ADR-015](../adr/015-multi-source-platform.md) |
| [multi-source-dataset-probe.md](./multi-source-dataset-probe.md) | nuScenes v1.0-mini and LeRobot v3.0 structure, timing, unit boundaries and revisions, read from real data | [ADR-015](../adr/015-multi-source-platform.md) |
| [multi-source-implementation-plan.md](./multi-source-implementation-plan.md) | M2–M5 work breakdown and acceptance criteria for ADR-015 (planning record) | [ADR-015](../adr/015-multi-source-platform.md) |
| [reference-verification-foundation.md](./reference-verification-foundation.md) | audit of the previous golden expectations; raw-source verification of the reference corpus; the independent verifier's checks and negative controls (no platform output verified) | [reference-verification.md](../development/reference-verification.md), [ADR-015](../adr/015-multi-source-platform.md) §12 |
| [execution-observability-study.md](./execution-observability-study.md) | execution health from durable state: SQL baseline, controlled workloads (backlog, slow handler, worker loss, lost dispatch), instrumentation | [jobs-and-pipelines.md](../architecture/jobs-and-pipelines.md) §5, [ADR-012](../adr/012-execution-health-from-durable-state.md) |

Old results are never rewritten when the implementation changes: a new report is added
instead. Implementation chronology (what changed and when) belongs to Git history.
