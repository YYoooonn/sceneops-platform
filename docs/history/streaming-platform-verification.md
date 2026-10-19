# Streaming Acquisition smoke verification (Step 7B-2)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules:
> [reference-verification.md](../development/reference-verification.md); disk policy and
> the capacity preflight: [local-development.md](../development/local-development.md#disk-hygiene);
> the disposable runtime: [test-matrix.md](../development/test-matrix.md#the-disposable-execution-runtime);
> decision: [ADR-015](../adr/015-multi-source-platform.md) §12. Preceded by
> [recording-platform-verification.md](./recording-platform-verification.md) (Recording
> Import, 10 fixtures).

```text
date         2026-10-19
branch       refactor/multi-source-platform
HEAD         083323b (working tree: this change, uncommitted; it changes no app, package
             or integration source)
host         macOS (Darwin 24.3.0), repository venv; Docker Desktop VM filesystem 58.4 GB,
             15.1 GiB free at the start
images       capture 862d54b9a9c7 and streaming-bridge 08f402144f22 rebuilt from the tree
             before the run (the previous ones predated abcff9e); dataset-replay
             49ebe15eec80, api 04bde89be407, worker a65c8187fb14, publisher d1b359424cdd
             already current
inputs       data/reference/nuscenes-mini-v1 (locked MCAPs), corpus.lock.json,
             reference_expectations.json (unchanged)
scope        one fixture, scene-0061 (smoke-1), Streaming Acquisition only: 1 RobotRun,
             1 Scene, 1 Episode, DatasetVersion
             sceneops-stream-ref-nuscenes-mini-full-10/baseline-scene-0061
not run      the 10-fixture streaming corpus, Recovery, cleanroom, learning journeys
```

## 1. Isolation

The run used the disposable execution runtime with its own streaming path
(`disposable_env.py run --streaming`, added here), twice, each from nothing:

| Resource | Used | Reference environment |
| --- | --- | --- |
| PostgreSQL database | `sceneops_test_ref7b2`, migrated to head | `sceneops`: not opened by the platform |
| MinIO bucket | `sceneops-test-ref7b2` | `sceneops`: not written |
| API, Celery workers, Redis | compose project `sceneops-test` (`compose/test-runtime.yaml`) | not used |
| Kafka, Kafka log, capture volume | `test-kafka` with `sceneops-test_test-kafka-data`, `sceneops-test_test-acquisition-recordings` (`compose/test-streaming.yaml`); replay, bridge, broker and Capture on the project's private network | no broker running, no Kafka volume; `sceneops_acquisition-recordings` untouched |
| Shared | the PostgreSQL and MinIO servers and the Docker VM disk | |

The bridge's acknowledged `RUN_START` was `sceneops.robot.telemetry.v1:0:0` in both runs:
offset 0 of a topic that did not exist before. A read-only fingerprint of the reference
environment (per table: row count and md5 of the ordered rows; the bucket's keys, sizes and
ETags; the bucket list; the `sceneops*` volumes) was identical before the first run, after
the first teardown and after the second. The reference environment was the freshly reset
state (26 tables, only `alembic_version`; empty bucket). After each teardown no container,
volume or network of `sceneops-test` and neither the database nor the bucket remained.

## 2. Procedure and results

Each run: capacity preflight → streaming bootstrap (`FIXTURE=scene-0061`) → `verify --deep
--mode streaming_acquisition --fixture scene-0061` → second bootstrap → `reconcile --once
--apply` → re-capture of the run from the retained Kafka records into a scratch root →
message-level comparison → `verify` again. Run 2 repeated run 1 with a corrected comparison
(§4).

| Step | Run 1 | Run 2 |
| --- | --- | --- |
| capacity preflight | fits: estimated growth 1.46 GB, required 5.05 GB, 15.1 GiB free | the same estimate |
| replay → bridge → Kafka → Capture | 8,897 messages, per topic as locked; bridge 0 failed; receipt finalized by `RUN_END`, offsets `RUN_START..RUN_END` = 8,899 records | the same |
| replay / capture / publish + register | 9.9 s / 23.8 s / 16.8 s | 9.8 s / 23.4 s / 15.5 s |
| Scene / Episode build | 15.6 s / 10.3 s | 15.5 s / 10.3 s |
| bootstrap total | 89 s | 86 s |
| `verify --deep` | 4,357 checks, **0 violations**, controls **25 / 25** (15 s) | the same |
| second bootstrap | 3 s; `reused, not replayed`, compose skipped; summary byte-identical | the same |
| `reconcile --once --apply` | state `registered`, no action | the same |
| `verify` after the idempotency passes | 3,751 checks, 0 violations, 25 / 25 | the same |

The deep verification read back and hashed the registered recording, re-derived its
canonical content and compared it with the derivation of the locked recording
(`streaming_content`), and checked unit identities, revisions, manifests, lineage and
producer fingerprints, all 606 payloads (224 front-camera images, 382 point clouds: record,
stored object, bytes) and the DatasetVersion (members exactly the fixture's two units,
pins, composition checksum, summaries).

Across the two runs the Scene and Episode ids were identical
(`scene-a79272d714b370d18392e1e34e10b6bf`, `episode-d9ffdcc44123eeff8f56ae8840637f0e`).
The captured recordings (355,634,742 and 355,572,977 bytes) and therefore the manifests'
recording binding and the composition checksums differed, as each capture records its own
receive times.

## 3. Replay convergence

The run's records were still in the broker after registration. A second Capture of the
same RobotRun from the acknowledged position, into a scratch root, produced a recording
with every message equal to the registered capture's (topic, order, header stamp, CDR
bytes, size, MCAP sequence) except `log_time` (all 8,897 differ), and its derived canonical
content equals the locked recording's. Nothing was published from it.

## 4. Message bytes against the locked recording

Run 1's comparison keyed messages on `publish_time` as well and reported 0 equal messages.
That was the comparison's error: a captured recording's `publish_time`, like its
`log_time`, is Capture's receive time. Keyed on topic, per-topic order, header stamp,
message sha256 and size, run 2 found **all 8,897 messages equal** to the locked recording's,
on all 20 topics; `publish_time` and `log_time` differ for every message. No verifier rule
compares those two fields of a captured recording.

## 5. Storage

| Measurement | Run 1 |
| --- | --- |
| Docker VM free: start / minimum during the bootstrap / after the bootstrap | 15.06 / 13.66 / 13.99 GiB |
| peak growth during the bootstrap (sampled every 5 s) | 1.40 GiB (preflight estimate of the growth: 1.36 GiB) |
| Kafka topic log after one run (`du`) | 461,064 KiB = 472.1 MB (estimate 1.33 R = 473.2 MB) |
| MinIO objects | 618 objects, 655,263,859 B: recording 355,638,433, payloads 298,096,503, manifests 1,521,405, run records 7,518 (estimate 1.78 R = 633.3 MB) |
| capture volume | 0.36 GB during capture; empty after the registered capture was released |
| disposable database | 11.3 MB; 1 RobotRun, 618 artifacts, 10 Jobs, 2 PipelineRuns |
| with the re-capture (§3) | peak growth 1.51 GiB |

The MinIO estimate was 3.4 % below the measurement: scene-0061's derived objects are
0.842 of its recording, above the 10-fixture Recording Import mean of 0.780 the
preflight had used, and manifests were not counted. The ratio was raised to 0.85 (derived
objects, manifests included) and a test pins the estimate at or above both measurements.
Run 2 started with 631 MiB of run 1's dropped bucket still in MinIO's data directory, as
already observed in 7B-1.

## 6. Changes made for this step

- `tools/dev/capacity_preflight.py` (`make capacity-preflight`), run by both baseline
  bootstraps before their first write; the canonical bootstrap had no check.
- `compose/test-streaming.yaml` and `disposable_env.py run --streaming`;
  `STREAMING_RUNTIME=disposable` in `streaming_lib.sh` / `streaming_bootstrap.sh`;
  `streaming_storage` also reports the PostgreSQL server's data size.
- `reference_verify.py verify --fixture` (`make reference-verify FIXTURE=`): one fixture's
  RobotRuns and its `baseline-<fixture>` DatasetVersion; the control context follows the
  first verified mode (a streamed run when only Streaming Acquisition is verified), and the
  two controls that took a second Scene member use the version's other unit when it holds
  one Scene.

No platform defect was found. Tests added: 11 preflight, 28 verifier (one-fixture
streaming scope, every control in it), 7 runtime wiring, 3 launcher.

## 7. Requirements for the full 10-fixture streaming run

- **Disk.** The preflight requires 18.7 GiB free in the Docker VM for the 10 streamed
  fixtures (estimated growth 12.6 GiB: MinIO 7.5 GB, Kafka 5.4 GB, one capture 0.56 GB)
  and refuses it at the 14.5 GiB free at the end of this run. Kafka keeps the log for 24 h,
  so it is part of the peak and is only released with the disposable runtime. Free space
  must be made by the user (build cache 25.5 GB reclaimable, `docker builder prune`), or
  the VM disk enlarged; nothing was removed here.
- **Both modes together** (`reference-contract-verify`, cleanroom): about 30 GiB free
  (11.8 + 18.7 GiB required by the two preflights).
- **Command.** The documented disposable command with `REFERENCE_SCOPE=nuscenes-mini-full-10`
  (no `FIXTURE`) and `verify --mode streaming_acquisition` without `--fixture`; the bootstrap
  composes the contract's `baseline` version. Measured per fixture here: ~1.5 min.
- **Not covered by this run:** convergence of an interrupted bootstrap (capture resume,
  publish-pending of a half-published run), Recovery, the joint two-mode verification,
  cleanroom.
