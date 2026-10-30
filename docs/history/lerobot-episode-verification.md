# LeRobot Episode verification (M3 S3)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules: the Episode model and the LeRobot builder in
> [episode-domain.md](../architecture/episode-domain.md), payload and clock rules in
> [ingress-boundaries.md](../architecture/ingress-boundaries.md) §4, the verifier's rules
> in [reference-verification.md](../development/reference-verification.md), the suites in
> [test-matrix.md](../development/test-matrix.md); decision:
> [ADR-015](../adr/015-multi-source-platform.md) §6 and its 2026-10-26 amendment;
> acceptance criteria: [multi-source-implementation-plan.md](./multi-source-implementation-plan.md)
> §4 (S3).

```text
date         2026-10-26
branch       refactor/multi-source-platform
HEAD         ef9320d (working tree: S3, uncommitted)
host         macOS 15.3.2 (Darwin 24.3.0), Apple M1 Pro, repository venv (Python 3.11,
             pyarrow 25.0.1)
docker       29.8.2; Docker Desktop VM filesystem 94 GB, 61–64 GB free during the runs;
             only the postgres / redis / minio servers of the local stack were started
             (the reference database was not migrated and stayed empty)
images       built here from the working tree: api 183bc5d50f5d, worker 11ec5e82b300
             (publish-snapshot ran as the host-side Publisher process)
inputs       data/raw/lerobot/pusht at 7628202a… (7 files, 7,684,382 B; the S1 lock)
config       config/reference/lerobot-pusht/episode_build_config.json
```

## 1. What was run

| Command | Result |
| --- | --- |
| `make test` | 21 suites: 2,658 passed, 114 skipped, 0 failed (before the last two edits; `apps/worker/tests` 421 passed and `tools/checks/tests` 20 passed again after them) |
| `make test-integration` (disposable database + bucket) | 317 passed (209 db + storage, 102 worker / analytics / execution incl. 5 content-Episode tests, 6 api) |
| `make test-infrastructure SUITE=lerobot-episodes` (disposable runtime, final images) | capacity preflight `ok` (63.3 GB VM free); 10 passed in 76.9 s |
| `make test-infrastructure SUITE=pipelines` (recording regression, disposable runtime) | 25 passed in 312 s (images from an earlier build of this tree: formatting and the inventory lookup of the SourceFileRef gate differed) |
| `make check`, `make lint`, `make check-boundaries`, `make check-commands` | pass |
| `alembic downgrade -1` / `upgrade head` on a disposable database | `f2c6a9e4b8d1` ⇄ `b6e2d8f4a1c7` |
| `lerobot_verify.py derive`, `selftest` (no stack) | 206 units, no refused unit; 11 / 11 controls detected |

## 2. Results against the S3 acceptance criteria

Every number below was produced twice: by the platform (builder → registration →
composition on the disposable runtime) and by the independent verifier
(`tools/reference/lerobot_verify.py`, its own pyarrow reader and its own
implementation of the documented rules); the verifier compared them field by field.

| # | Criterion | Observed |
| --- | --- | --- |
| 1 | 206 Episodes, unit keys "0"…"205", `length` occurrences per selected stream | 206 units; 25,650 observations, states and actions each; 76,950 transitions (3 × 25,650); 206 task events |
| 2 | Episode 0: 161 frames, `frame_timeline {lerobot.frame_time, 10, 161}`, window `[0, 16000000001)`, origin `unit`, timestamps `round_half_even(k · 10⁹ / 10)`, no timestamp / frame_index value | as stated; every occurrence is `k · 100,000,000` ns; values carry only `value` (task events `task`, `task_index`) |
| 3 | state / action values bit-equal to the Parquet float32; `component_names ["motor_0", "motor_1"]` | equal and float32-exact on all 206 units; both fields declare the names |
| 4 | image occurrences are `SourceFileRef`s (`video/mp4`) to `videos/observation.image/chunk-000/file-000.mp4` (`sha256:f58d1185…`), episode 1 frame 0 → 16,100,000,000 ns | as stated; the stored video object has the inventory size |
| 5 | one task event at 0 per Episode; `next.*` transitions one per frame; `next.done` true on 412 rows, `next.reward` > 0 on 20,725, `next.success` never; no event derived | as stated (the last two frames of every episode carry `next.done = true`) |
| 6 | no snapshot id, upstream, upstream revision or licence in any manifest; a second snapshot reuses every revision and its Job result names it | no such JSON token in any of the 206 manifests; through snapshot B: 206 existing revisions, 0 manifest records created, no object written; result `snapshot_id` = B; the manifest records keep the first build's Job |
| 7 | rejected loudly with no row: non-grid timestamp, unsupported `codebase_version`, unknown `unit_keys` entry, `SourceFileRef` checksum ≠ inventory, referenced object missing | off-grid and v2.1 copies registered as preserved content and their builds failed ("is not float32", "not supported") with no Episode; `unit_keys ["0","206"]` failed with no object written; the forged checksum and the deleted video were rejected by `REGISTER_EPISODES` on real MinIO + PostgreSQL with no revision (`test_content_episode_integration.py`) |
| 8 | rebuilding adds no revision, artifact or object; alignment and the two exports refuse | rebuild: 0 manifests created, 206 existing revisions, identical object listing; `ALIGN_EPISODE` of a pusht member failed with `UnsupportedEpisodeContentError` (transition streams); the learning and LeRobot exports read only aligned artifacts, so no pusht Episode reaches them |

DatasetVersion: the pipeline's compose stage pinned all 206 revisions
(`episode_count` 206, no Scene summary, no RobotRun involved); a
`COMPOSE_DATASET_VERSION` by the selector `{episode, content, <content_id>,
<fingerprint>}` produced the same `composition_checksum`.

## 3. The independent verifier

| Run | Scope | Checks | Violations | Controls detected |
| --- | --- | --- | --- | --- |
| `--episode 0 verify` after the Episode 0 pipeline | Episode 0 | 43 | 0 | 11 / 11 |
| `verify --dataset sceneops-test-lerobot-pusht/pusht-all` after the full pipeline | all 206 units + the DatasetVersion | 7,631 | 0 | 11 / 11 |

The derivation and the platform agree on `content_id` `content-0322a8b9f1c6ad988b7f45714f36938c`.

Negative controls, each applied consistently (manifest re-serialized, checksum,
artifact id, revision projection, member pin and composition checksum recomputed) to a
copy of the passing observation, all detected: `timestamp_drift`, `clock_origin`,
`absolute_anchor`, `vector_order`, `transition_value`, `dropped_occurrence`,
`content_binding`, `source_file_checksum`, `frame_selector`, `missing_native_file`,
`snapshot_in_manifest`.

The verifier and the platform builder were also cross-checked offline on synthetic
LeRobot v3.0 trees at 30 fps with a task change, for both `names` forms and both list
types (`tools/reference/tests/test_lerobot_verification.py`): no disagreement.

## 4. Measurements

| Step (disposable runtime, one worker of each kind) | Seconds |
| --- | --- |
| publish-snapshot + `REGISTER_SOURCE_SNAPSHOT` (pusht, first registration) | 1.8 |
| `snapshot_episode_building`, Episode 0 (build, register, validate) | 3.1 |
| `snapshot_episode_building`, all 206 (build, register, compose, validate, profile) | 21.1 |
| `BUILD_SNAPSHOT_EPISODES` rebuild of 206 (no write) / `REGISTER_EPISODES` re-run | 6.1 / 4.1 |
| verifier, Episode 0 / all 206 with the DatasetVersion (incl. collection and controls) | 0.9 / 17.4 |

Offline, on the host: the builder plans all 206 Episodes in 2.5 s; Episode 0's manifest
is 201,441 bytes and strict-loads in about 11 ms; the 206 manifests total 32.5 MB.

Scope: one host, a 7.7 MB dataset with one data file, one video file and one task. These
are observations of this run, not capacity limits.

## 5. Findings

- No platform defect was found by the verifier.
- Manifest schema versioning: the Episode changes ship as `episode_manifest/v3` (v2 was
  the S2 clock-domain manifest); recording Episode manifests change bytes (new optional
  members serialize as `null` / `[]`) and the recording Episode producer's semantics
  version moved 3 → 4. Scene manifests are byte-identical.
- Stale active documentation fixed in passing: the verification guide said manifests
  declared no clock origin (true before S2).

## 6. Not covered

- Video frames are referenced, never decoded; AV1 frame-exact decoding is unverified.
- Multi-file and multi-task LeRobot datasets (OQ-8) and LeRobot v2.x.
- Alignment / learning export of LeRobot-sourced Episodes (M4).
- The reference contract and golden expectations are not regenerated here (S5).
