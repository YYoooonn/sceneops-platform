# Recovery and data integrity verification (Step 7B-3)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules: Capture restart behaviour in
> [streaming-transport.md](../architecture/streaming-transport.md) ("Restart and failure
> behavior"), the streaming bootstrap's create-or-verify in
> [canonical-baseline.md](../development/canonical-baseline.md), the verifier in
> [reference-verification.md](../development/reference-verification.md); decisions:
> [ADR-008](../adr/008-acquisition-lifecycle-reliability.md),
> [ADR-013](../adr/013-capture-records-no-kafka-progress.md),
> [ADR-015](../adr/015-multi-source-platform.md) §12. Preceded by
> [streaming-platform-verification.md](./streaming-platform-verification.md).

```text
date         2026-10-19
branch       refactor/multi-source-platform
HEAD         53a9337 (working tree: this change, uncommitted)
host         macOS (Darwin 24.3.0), repository venv; Docker Desktop VM filesystem 58.4 GB,
             16 GiB free at the start
images       worker rebuilt from the tree after the reconciler fix (§3.1), 2f886ecbceb9;
             capture 862d54b9a9c7, streaming-bridge 08f402144f22, api 04bde89be407,
             publisher d1b359424cdd, dataset-replay 49ebe15eec80 unchanged (no source
             change since they were built)
inputs       data/reference/nuscenes-mini-v1 (locked MCAPs), corpus.lock.json,
             reference_expectations.json (unchanged)
scope        one fixture, scene-0061, Streaming Acquisition; interrupted at the capture, the
             bridge, the publication and the Scene build. The infrastructure recovery suite.
not run      the 10-fixture streaming corpus, cleanroom, `SUITE=kafka` against the reference
             broker (its suites were run against a disposable broker instead, §4)
```

## 1. Isolation

Each scenario ran from nothing on its own disposable streaming runtime
(`disposable_env.py run --streaming`): database `sceneops_test_ref7b3<x>`, bucket
`sceneops-test-ref7b3<x>`, compose project `sceneops-test` with its own API, workers, Redis,
broker, Kafka log and capture volume. The recovery suite used `sceneops_test` /
`sceneops-test` and throwaway Redis containers. A read-only fingerprint of the reference
environment (per table: row count and md5 of the ordered rows; the bucket's keys, sizes and
ETags; the bucket list; the `sceneops*` volumes) was identical before the first run and
after the last; the reference environment was the reset state (26 tables, only
`alembic_version`; empty bucket). After each scenario no container or volume of
`sceneops-test` remained. A watchdog sampled the Docker VM every 5 s (minimum 12.93 GiB
free; it never acted).

Faults were injected with `docker kill` (SIGKILL) on the named container, or SIGKILL to the
bootstrap's process group. The driver, the fingerprint and the publisher-kill poller were
scratch scripts, not repository code.

## 2. Scenarios and results

The expected state after recovery is the uninterrupted run of 7B-2: 1 RobotRun, 618
artifacts, Scene `scene-a79272d714b370d18392e1e34e10b6bf` and Episode
`episode-d9ffdcc44123eeff8f56ae8840637f0e` with one revision each, one DatasetVersion of
two members, 2 PipelineRuns, 10 Jobs (each type once), `verify --deep` 4,357 checks.

| | S1 Capture killed | S2 bridge killed | S3 publisher killed, then bootstrap killed in the Scene build |
| --- | --- | --- | --- |
| fault | Capture SIGKILLed 5 s into the replay; bridge and replay completed | bridge SIGKILLed 4 s into the replay | publisher SIGKILLed 3.4 s into `publish-pending`, as soon as `recording.mcap` existed and before the manifest; then the re-run bootstrap SIGKILLed 31 s in, Scene pipeline `running` |
| first bootstrap | fails 37 s: `capture exited non-zero` | fails 48 s: `bridge exited non-zero` | fails 46 s: `publish-pending failed` |
| left behind | Kafka 8,899 records (RUN_START, 8,897, RUN_END); `.partial/<run>` only; state `capture_unfinished`; no RobotRun, 0 objects, 0 rows | Kafka 1,911 records, no RUN_END; `.partial/<run>`; `capture_unfinished`; no RobotRun, 0 objects | finalized bag + receipt; recording object only; state `publication_incomplete`; no RobotRun |
| recovery | re-run bootstrap: `capture_resumed` (Capture without a start position, scan from offset 0), 8,897 messages, published, registered, built, composed (72 s) | re-run: resumed Capture aborts on its idle guard (73 s), nothing finalized, published or registered. Partial bag removed by hand; re-run: `absent` → streamed again under the same run id, RUN_START at offset 1911, receipt check passed (90 s) | re-run: `publish-pending` from the receipt reused the recording (`recording_written: false`), wrote the manifest, `reconcile --apply` submitted, registered. Bootstrap after the kill: first attempt **failed** (§3.2); after the fix it waited for the running Scene pipeline, built the Episode and composed (26 s) |
| `verify --deep` | 4,357 checks, 0 violations, 25 / 25 controls | the same | the same |
| idempotency | bootstrap again: reused, 2 s, summary byte-identical; `reconcile --apply`: `registered`, no action | reused, 3 s, identical | reused, 3 s, identical |
| final state | as expected | as expected | as expected (one Scene PipelineRun: the interrupted one) |
| `verify` after | 3,751 checks, 0 violations, 25 / 25 | the same | the same |

In S2 the topic held the aborted session (offsets 0–1910) and the complete one (1911–10809).
A Capture of the run without a start position afterwards failed with `RunLifecycleError:
RUN_START repeats the previous control envelope but is not an immediate redelivery of
RUN_START` and finalized nothing.

What this shows for the five properties of the step, on this fixture: no observation was
missing or duplicated (deep verification against the raw-source derivation, every payload
hashed); the recording was finalized only on a complete RUN_START…RUN_END session, never
from a truncated one; unit ids and RevisionPins (one revision per unit, the member pins and
the composition checksum recomputed by the verifier) did not change across interruptions;
re-registration and re-composition were no-ops; Jobs and PipelineRuns ended `succeeded`,
each once.

## 3. Defects found and fixed

### 3.1 `reconcile --apply` hung on an unresponsive broker

`make test-infrastructure SUITE=recovery` stopped at
`test_an_unresponsive_broker_does_not_hang_a_pass` (Redis paused): no progress for more than
8 minutes against the test's 90 s bound. Run alone with `faulthandler_timeout`, the pass was
blocked in `Celery.send_task` → `RedisBackend.on_task_call` → result-channel `subscribe` →
connection health check, reading a socket with no effective timeout.

`build_registration_submitter` bounded the result backend through
`result_backend_transport_options = {socket_timeout, socket_connect_timeout}`. The Redis
result backend (celery 5.6.3) takes its socket bounds only from `redis_socket_timeout`
(default 120 s) and `redis_socket_connect_timeout`, and reconnects under its retry policy
(default 20 attempts). Setting the two keys to the reconciler's 5 s bound made the pass end
after 130 s (20 reconnects); limiting the backend's retry policy to one reconnect made it
end after 15.5 s, with `DISPATCH_FAILED` and the Job left queued for the next pass.

Fix: `apps/worker/sceneops_worker/cli/acquisition/reconcile.py`. Regression: the existing
real-Redis test now passes; `apps/worker/tests/robots/test_reconcile_dispatch_bounds.py`
reads the values the constructed backend uses (`120.0 == 5` and `20 == 1` fail without the
fix).

### 3.2 An interrupted bootstrap could not resume while its pipeline was running

`run_pipeline` (`tools/e2e/lib.sh`) creates the PipelineRun with `force: false`, which
returns an identical pending, queued, running or succeeded run, and dispatched every
returned run that was not `succeeded`. For the running Scene pipeline the API refused the
execute (HTTP 400, `validate_executable`), and the streaming bootstrap stops on a rejected
execute: re-running a killed bootstrap failed until the orphaned pipeline had finished. The
platform behaved as specified; the harness did not.

Fix: a returned `running` run is waited for, not dispatched. Regression:
`tools/e2e/tests/test_run_pipeline.py` (a stubbed `curl`; the running case fails without the
fix). S3 was repeated from nothing with the fix (results above).

## 4. Test runs

| Command | Result |
| --- | --- |
| `make test-infrastructure SUITE=recovery` before the fix | hung at the 9th test (§3.1); stopped |
| the same after the fix | 24 passed (278 s) |
| capture suite (`packages/sceneops-recording/tests/capture`) in the capture image against the disposable broker | 185 passed, including the new `test_a_run_streamed_again_after_an_aborted_session_is_captured_from_its_new_run_start[0, 3]` |
| `tests/streaming` (real bridge + Capture) in the capture image against the disposable broker | 6 passed |

## 5. Remaining failure scenarios

- **A run whose RUN_END never reached Kafka** cannot be recovered from Kafka. The partial
  bag is removed by hand and the fixture is streamed again. The aborted session stays in the
  log until retention, so a later scan-from-the-beginning resume of the same run fails at
  the second RUN_START (loud, nothing finalized) and needs another stream; a resume that
  used the new RUN_START position would succeed, but the bootstrap keeps no position.
- **A resumed capture** is checked by `streaming-verify` (counts) and the reference verifier
  (content), not by the bootstrap's replay / bridge / receipt-offset checks.
- **Not exercised on the streaming path:** a worker killed during registration or a build,
  a registration stalled past the 900 s threshold, the broker or Redis lost during the
  bootstrap, a host restart, two bootstraps at once. Registration under worker loss, lost
  messages and the attempt budget are covered by the recovery suite on synthetic
  recordings.
- **A queued run whose execute races its worker** (returned `queued`, `running` by the time
  `run_pipeline` dispatches it) is still refused with HTTP 400; not observed.
- One fixture only; the interruption points were chosen, not exhaustive.

## 6. Requirements for full Streaming and Cleanroom verification

Unchanged from [streaming-platform-verification.md](./streaming-platform-verification.md)
§7: the preflight requires 18.7 GiB free in the Docker VM for the 10 streamed fixtures and
about 30 GiB for both modes (the cleanroom); 16 GiB were free here. The worker image must be
rebuilt from this tree (`make compose-build`) before either run, so that the reconciler
carries the fix of §3.1. An interrupted 10-fixture run now resumes by re-running the
bootstrap, except in the RUN_END case above.
