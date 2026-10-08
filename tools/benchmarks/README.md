# Benchmarks

Measurement tooling. A benchmark reports numbers for one workload on one machine; it
asserts nothing about correctness and is **not an acceptance gate**. Correctness is
proved by the commands of [docs/development/test-matrix.md](../docs/development/test-matrix.md)
(`make test`, `make test-integration`, `make test-infrastructure`, the `make e2e-*`
journeys). Nothing here is part of `make test`, `make check-commands` or any of those
commands, and no Make target runs a benchmark.

Results are recorded as point-in-time evidence (date, HEAD, environment, workload,
configuration, timings), in the study documents under `docs/architecture/`, and claim only
what was measured: "validated through 100k messages" is not "supports at most 100k".

| Path | Subject | Runs on | Needs |
| --- | --- | --- | --- |
| `learning_data/benchmark_phase5_final.py` | learning-data access at scale: sharded layout, selective reads, bounded cache, incremental export, together | host | nothing (local artifact store, synthetic fixture) |
| `learning_data/benchmark_selective_reads.py` | I/O saved by selective EpisodeRef / window reads | host | nothing |
| `learning_data/benchmark_cache_and_bulk_access.py` | bounded cache and shard-aware bulk access | host | nothing |
| `learning_data/benchmark_incremental_export.py` | write amplification of incremental vs full-rebuild export | host | nothing |
| `learning_data/benchmark_minio_access_strategy.py` | the same access strategies against real MinIO | host | `make local-up`; writes under a throwaway prefix of the configured bucket |
| `streaming/benchmark_streaming_capture_scale.sh` (+ `.py`) | capture throughput, Kafka lag and memory vs message count and payload size | `capture` container + host | Kafka (`make streaming-up`) |
| `execution/benchmark_execution_metrics_sql.py` | cost of the execution-health queries over a synthetic history (200 000 Jobs by default) | host | a disposable database (`disposable_env.py create --database sceneops_test_<name>`); TRUNCATEs its execution tables |
| `execution/execution_observability_experiments.py` (+ `load_worker.py`) | healthy / backlog / slow handler / worker loss / lost dispatch workloads on real workers, observed through the execution-health metrics and checked against ground truth | host (Celery subprocesses, throwaway Redis) | Docker, `make local-up` (PostgreSQL), a disposable database; TRUNCATEs its execution tables per scenario |
| `acquisition/benchmark_registration_latency.py` | `REGISTER_ROBOT_RUN` latency through the production path | host + the stack's worker | `make local-up`, the prepared recording; registers throwaway `bench-reg-*` RobotRuns |

```bash
uv run python tools/benchmarks/learning_data/benchmark_phase5_final.py --scales tiny,small --out /tmp/report.json
tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 30000 64      # MESSAGE_COUNT [PAYLOAD_BYTES]
```

Each script's docstring states its workload, options and prerequisites.

## Rules

- A benchmark that writes to PostgreSQL, MinIO or Kafka (the last six rows) does not run
  on a reference environment that must stay equal to the golden contract: use a
  disposable runtime, or accept that `make local-reset` + `make reference-contract-bootstrap`
  rebuilds the reference environment afterwards.
- Benchmark first, then optimize: separate implementation overhead from architectural
  overhead, and record the environment next to every number.
- A benchmark that is superseded (its decision was taken, or a later benchmark covers its
  workload) is deleted; its result stays in the study document that recorded it.
- Study documents written before this directory existed cite benchmark scripts under `scripts/dev/`
  and `scripts/dev/phase7/` (the former `scripts/` directory). The ones still needed are listed above; the others (including the
  multi-run router prototype and benchmark) were removed with the experiment they measured and
  remain in Git history.
