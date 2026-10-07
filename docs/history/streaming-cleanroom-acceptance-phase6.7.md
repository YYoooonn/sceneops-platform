# Streaming Clean-Room Acceptance (Phase 6.7)

> **History — point-in-time record, not current architecture.** The `make e2e-*`
> commands, services and acceptance flow named below were consolidated: the supported
> command surface is `make help` and [Test matrix](../development/test-matrix.md), and the
> streaming contract is [Streaming transport](../architecture/streaming-transport.md).
> References to Airflow describe a component that is no longer part of the platform
> ([ADR-009](../adr/009-job-centric-execution-and-durable-boundaries.md)).

> Point-in-time record (category C — historical evidence, per this repo's
> documentation taxonomy) of the final Phase 6 acceptance pass: the full
> streaming pipeline (real nuScenes CAN → ROS2 → Kafka → durable MCAP
> capture → ArtifactStore → canonical RobotRun → Episode → learning
> export → `SceneOpsDataset`) run end to end from a freshly reset local
> stack, with real source data. Not a living architecture doc — see
> [Streaming transport](../architecture/streaming-transport.md),
> [Streaming reliability & scale baseline](./streaming-reliability-scale-baseline.md),
> and [Robot run / MCAP](../workflows/robot-run-and-mcap.md) for the
> current, maintained contracts this record validates. Numbers here are
> frozen to the date/commit/environment below.

**Date:** 2026-09-30
**Commit (HEAD throughout this acceptance pass):** `df55d85a511143a5556fff0390f151b3d638f91e` (branch `feat/streaming-robot-data`)
**Environment:** macOS 15.3.2 (Darwin 24.3.0, arm64), Docker 29.4.1, 8 logical CPUs, single-node local Compose stack (`apache/kafka:3.9.2` KRaft mode, 1 broker, 1 partition on `sceneops.robot.telemetry.v1`; Postgres 16; MinIO). Same class of environment as the Phase 6.6 reliability baseline — not production/multi-broker/multi-partition.
**Source scene:** `scene-0061` (real nuScenes v1.0-mini CAN bus data), the deterministic default used by every streaming E2E target.

---

## 1. Verdict

```text
PASS — Phase 6 streaming pipeline is clean-room accepted
```

From a fresh reset of all generated platform state (Postgres/Redis/MinIO/Kafka volumes wiped; real nuScenes source data and the pre-existing direct-recorded rosbag baseline preserved), the full chain

```text
real nuScenes CAN → ROS2 → Kafka → durable MCAP capture → ArtifactStore
  → ArtifactRecord → canonical RobotRun → materialization → Episode
  → alignment/validation/profile → LearningDataExport → SceneOpsDataset
```

completed successfully, end to end, three times over (once per E2E target composing progressively more of the chain), using only real infrastructure and real source data. Every frozen contract listed in the acceptance brief held. One pre-existing staleness issue was found and fixed before the run (see §11) — no pipeline logic bugs were found during the run itself.

---

## 2. Starting / final git state

- Branch: `feat/streaming-robot-data`, HEAD `df55d85a5111...` throughout (no commits made during this acceptance pass, per instruction).
- Working tree: clean at start and at end (`git status` — nothing to commit).
- No git commit was created as part of this work.

---

## 3. Clean-room reset audit

**Preserved (real source data, never touched):**

- `data/raw/nuscenes/` — full nuScenes v1.0-mini + CAN bus expansion (9.4G, 7,832 CAN JSON files across 10 scenes).
- `data/raw/rosbag/` — 10 pre-existing direct-recorded (`ros2 bag record --storage mcap`) baseline MCAPs, including `scene-0061`, used read-only as the registration conflict-test fixture and as one comparison point.

**Reset (generated platform/runtime state):**

- Postgres volume (`sceneops_postgres-data`) — dropped and recreated via `make local-reset` (`docker compose ... down -v`), migrations re-applied from scratch.
- Redis volume (`sceneops_redis-data`) — dropped and recreated.
- MinIO volume (`sceneops_minio-data`) — dropped and recreated; buckets re-initialized by `minio-init`.
- Kafka volume (`sceneops_kafka-data`) — **not** covered by `local-reset` (different Compose profile: `streaming`, not `worker`/`tools`/`debug`) — reset explicitly and separately: `docker compose --profile streaming down kafka -v`, then `make streaming-up` recreated it fresh.
- `data/datasets/`, `data/runs/`, `data/models/`, `data/artifacts/` — emptied and recreated (empty) by `local-reset`.
- No leftover scratch dirs found outside Docker volumes (`data/tmp_streaming_capture/`, `data/tmp_robot_run_registration/`, `data/tmp_robot_run_learning/` did not exist pre-reset; each E2E script itself `rm -rf`s its own scratch root before writing).

**Images rebuilt** (staleness audit, §11): `api`, `worker` (`make compose-build`), `ros2` (`docker compose build ros2` — this one mattered, see §11), `nuscenes-integration`.

**Airflow** — untouched; out of scope for the streaming pipeline (its own Postgres volume and lifecycle are independent, per `local.mk`/`reset_local_state.sh`'s own documented scope).

---

## 4. Baseline bootstrap / verification result

`make canonical-bootstrap` against the freshly reset stack: **PASSED** (wall time 11m58s). Created and verified all three frozen v0.0 baselines from real nuScenes data:

| Baseline | scene_count | episode_count | readiness | Learning export / `SceneOpsDataset.open()` |
|---|---|---|---|---|
| `sceneops-scenes/v0.0` | 10 | 0 (no Episode activity, as expected) | ready | n/a |
| `sceneops-episodes/v0.0` | 0 (no Scene activity, as expected) | 10 | n/a | manifest + shard checksums verified |
| `sceneops-canonical/v0.0` | 10 | 10 | ready | opened; 10/10 episodes; first/middle/last step reads OK |

Cross-dataset identity isolation confirmed (Scene ids and Episode ids fully disjoint between the three baselines). `make canonical-verify` (read-only) run immediately after: **PASSED** (2.6s) — used as the pre-streaming snapshot for §8's isolation check.

---

## 5. Full streaming E2E result

All four streaming E2E targets run fresh (new `robot_run_id` per invocation, no reuse of any prior RobotRun/MCAP/ArtifactRecord/Episode/export), in sequence, against the same reset stack:

| Target | Result | Checks | Wall time |
|---|---|---|---|
| `make smoke-streaming` | PASS | 39 / 0 failed | 6.8s |
| `make e2e-ros2-streaming` | PASS | 32 unit tests + 34 verification checks / 0 failed | 50.2s |
| `make e2e-streaming-capture` | PASS | 55 unit tests + 8 verification checks / 0 failed | 1m28.0s |
| `make e2e-robot-run-registration` | PASS | 12 verification checks + idempotent-retry + conflict-refusal / 0 failed | 1m25.2s |
| `make e2e-robot-run-learning` | PASS | 11-stage pipeline + retry / 0 failed | 1m15.0s |

Total streaming E2E wall time: ~5m5s.

---

## 6. Boundary-by-boundary result

### ROS2 → Kafka (`e2e-ros2-streaming`, `robot_run_id=run-ros2-streaming-1790762672-50114`)

- All 5 expected channels observed; exact CDR decodability confirmed via real `rclpy.deserialize_message` for every one of 2,915 published messages.
- `sequence_number` 0..2914, no gaps, Kafka-offset order == publish order.
- All messages landed in exactly one Kafka partition.
- Real-observation channels' `source_timestamp_ns` matched the real CAN source JSON's `utime` set exactly (938 `/vehicle/odom`, 1899 `/vehicle/imu`, 38 `/vehicle/status`, 38 `/vehicle/control`, all checked). `/mission/status`'s synthetic timestamps confirmed disjoint from every real CAN timestamp.
- Published=2915, failed=0.

### Kafka → MCAP (`e2e-streaming-capture`, `robot_run_id=run-streaming-capture-1790762744-50175`)

- Captured MCAP finalized only — no `.partial` directory survived (verified by construction: `run_capture()`'s ordering plus the passing `finalize`/durability unit tests).
- `message_count=2914` == bridge's own published count; `first_sequence=0`.
- `sha256=71e5e28a1bbd5f26d348a18817b56dca768a4a34ff7ddc6356cfceb7f17e883f`.
- `RosbagAdapter` opened the captured MCAP without error; `robot_states`/`missions` non-empty.
- Semantic comparison against a fresh, independent direct `ros2 bag record` of the same scene: same topic/schema set; `robot_state` count within tolerance (two independently-timed replays); mission count and `mission_ids` matched exactly.

### MCAP → canonical RobotRun (`e2e-robot-run-registration`, `robot_run_id=run-registration-1790762846-50283`)

- `ArtifactRecord` created exactly once; `RobotRun` created exactly once; `RobotRun.mcap_uri == artifact.uri`; checksum agreement confirmed both from Postgres and from an independent MinIO byte read-back (`sha256=0f0141b5...`).
- Retrieved bytes reopened via `RosbagAdapter` independently of the registration path — all 5 channels present, `robot_states`/`missions` non-empty.
- **Idempotent retry** (same file, same `robot_run_id`): `created=False`, identical `artifact_id`/`uri`/checksum — nothing new written.
- **Conflict test** (different real MCAP — the preserved `data/raw/rosbag/scene-0061` baseline — under the same `robot_run_id`): correctly refused with `RobotRunRegistrationConflictError`, existing canonical state left untouched.

### RobotRun → Episode (`e2e-robot-run-learning`, `robot_run_id=run-learning-1790762944-50382`)

- MCAP read from the canonical ArtifactStore path (`s3://sceneops/artifacts/robot_runs/...`), not the original capture scratch path — `robot_run_id` only, no `mcap_uri` override, forcing resolution through the real materialization boundary.
- `whole_run` segmentation selected (per the frozen streaming-capture rationale — `/mission/status`'s synthetic replay time never overlaps CAN-derived real time for a Kafka-captured MCAP).
- Exactly 1 Episode created (`...-episodes-episode0000`); `GET /episodes?robot_run_id=...` linkage confirmed 1:1.
- Temp file cleanup: implicit by construction (execution-scoped `tempfile`, no persistent volume backing the worker container's OS temp dir — same reasoning as Phase 6.6's item G) and confirmed indirectly: the source `RobotRun` artifact's checksum was unchanged after materialization ran (§ below), meaning materialization only ever read the canonical object, never mutated or leaked its scratch copy back into canonical state.

### Episode → LearningData

- `align_episode` → `profile_aligned_episode` → `validate_aligned_episode`: all succeeded.
- `export_learning_data` (v2-sharded): succeeded, `episode_count=1`, `manifest_artifact_id=art-758164a37e80`.
- `scripts/canonical/verify_learning_export.py --open-dataset`: `sceneops_dataset_opened`, `sceneops_dataset_len_matches`, `first_middle_last_episode_reads_ok` all true. Episode `step_count=98`, `observation_dims=9`, `action_dims=0`, `observation_channels=state.acceleration,state.battery,state.orientation,state.position,state.velocity`, `action_channels=brake,steering,throttle`.
- Canonical source MCAP checksum unchanged after the pipeline ran (`sha256:1f947884...` before and after).
- Retry (`force=true` re-dispatch of `raw_log_episode_building`): succeeded, canonical MCAP checksum still unchanged.

---

## 7. Key counts / checksums

| Artifact | Value |
|---|---|
| ROS2→Kafka run | `run-ros2-streaming-1790762672-50114`, published=2915, failed=0 |
| Kafka→MCAP capture | `run-streaming-capture-1790762744-50175`, message_count=2914, sha256=`71e5e28a1bbd5f26d348a18817b56dca768a4a34ff7ddc6356cfceb7f17e883f` |
| RobotRun registration | `run-registration-1790762846-50283`, artifact checksum=`sha256:0f0141b532bbe7016291e718116bbdec709384f463a68662934ca37591b21b70` |
| RobotRun→Episode→Learning | `run-learning-1790762944-50382`, MCAP checksum=`sha256:1f947884649b8f1b55bd8b6e06358c0e863c365a5fbd8025eab8a8a131396d3c` (unchanged through pipeline + retry), episode `test-e2e-robot-learning-1790762944-50382-test-v1-episodes-episode0000`, manifest=`art-758164a37e80` |
| Canonical baseline (pre- and post-streaming, identical) | `sceneops-canonical/v0.0`: scene_count=10, episode_count=10, `sceneops-canonical-v0.0-episodes-mission-scene-0061` step_count=25, observation_dims=9, action_dims=0 |

---

## 8. Direct-path semantic comparison

Two independent comparisons were made against the real `ros2 bag record` direct path for `scene-0061`:

1. **Transport/capture-level** (`e2e-streaming-capture`'s own built-in stage 4/5): a fresh direct recording of the same scene compared against the Kafka-captured MCAP via `RosbagAdapter` — topic/schema set identical, `robot_state` counts within the expected cross-replay tolerance, mission count and `mission_ids` exactly equal.

2. **Learning-data level** (this acceptance's own cross-check): the streaming path's `scene-0061` episode (`whole_run` segmentation, `step_count=98`, `observation_dims=9`, `action_dims=0`) compared against the canonical baseline's direct-path `scene-0061` episode (`mission_boundary` segmentation, `step_count=25`, `observation_dims=9`, `action_dims=0`, from `canonical-bootstrap`'s own verified output). **Observation/action dimensionality is identical** between the two paths for the same scene — confirming the streaming path produces the same learning-relevant schema as the direct path. **Step count differs** (98 vs 25), which is the expected, already-documented consequence of `whole_run` vs `mission_boundary` segmentation (the former samples the entire run at the alignment frequency; the latter restricts to the mission window) — not a representational defect. `action_dims=0` on both paths for this specific scene is a `scene-0061` characteristic (confirmed by checking the canonical baseline's other scenes, e.g. `scene-0796`/`scene-1100`, which show `action_dims=3` on the direct path), not a streaming-path artifact.

No byte-identical-MCAP, identical-ingest-timestamp, identical-synthetic-mission-event-timestamp, or identical-artifact-ID equality was required or attempted, per the acceptance brief.

---

## 9. Retry / idempotency result

- Capture/finalization: durability-ordering and crash-boundary behavior already covered by Phase 6.6's reliability matrix (items C/D) and its regression tests — re-run as part of `make test`/`make test-integration` (§10) with no new gaps found; not re-expanded here per instruction.
- RobotRun registration: exact-retry idempotent (`created=False`, no new state); conflicting-checksum retry under the same `robot_run_id` correctly refused (§6).
- Episode/learning pipeline: `force=true` retry of `raw_log_episode_building` succeeded, re-ran materialization, left the canonical MCAP checksum unchanged (§6).
- No duplicate `ArtifactRecord`, no duplicate `RobotRun`, no corrupt replacement, and no unintended duplicate `Episode` state was observed in any retry.

---

## 10. Canonical-baseline-unchanged result

`make canonical-verify` run both immediately after `canonical-bootstrap` (pre-streaming) and again after the full streaming E2E + full regression suite (post-streaming): **byte-identical** — same `scene_count`/`episode_count` for all three v0.0 baselines, same sampled episode IDs, step counts, and dimensions, cross-dataset identity isolation still holding. Zero drift.

---

## 11. Bugs discovered / fixes made

**One issue found — a stale build artifact, not a pipeline logic bug.** During the pre-reset audit, the `ros2` Docker image (`sceneops-platform/ros2:local`) was found to predate the two most recent commits on this branch (`40359b45...`, `df55d85a...`), which changed `ros2/capture/capture_consumer.py` and added `ros2/capture/group_id.py` — the run-scoped, deterministic-per-`robot_run_id` consumer-group derivation that is the actual subject of this branch's last fix. Running the acceptance E2Es against the stale image would have silently exercised pre-fix capture-consumer logic. **Fix:** rebuilt `ros2`, `api`, `worker`, and `nuscenes-integration` images from current source before the clean-room reset; confirmed `ros2/capture/group_id.py` present inside the rebuilt image. This is a build-hygiene fix, not a code change — no regression test added, no commit needed (no source changed).

No other bugs were found during the clean-room run itself — every stage passed on the first attempt.

---

## 12. Regression results

| Target | Result |
|---|---|
| `make lint` | PASS (0.13s) |
| `make test` | PASS — 1419 passed, 14 skipped, 0 failed (8.9s) |
| `make test-integration` | PASS — 65 passed, 0 failed (5.0s) |
| `make smoke-streaming` | PASS — 39/0 failed |
| `make e2e-ros2-streaming` | PASS — 32 unit + 34 verification / 0 failed |
| `make e2e-streaming-capture` | PASS — 55 unit + 8 verification / 0 failed |
| `make e2e-robot-run-registration` | PASS — 12 verification + retry + conflict / 0 failed |
| `make e2e-robot-run-learning` | PASS — 11-stage pipeline + retry / 0 failed |
| `make canonical-verify` (post-regression) | PASS — baseline still unchanged |

---

## 13. Major stage timings

| Stage | Wall time |
|---|---|
| Image rebuild (api/worker/ros2/nuscenes-integration) | ~1m (not on the pipeline's critical path; one-time per stale-image audit) |
| Clean-room reset (`local-reset` + Kafka volume reset) | ~1m (dominated by MinIO re-seeding `data/raw` — 9.34 GiB at 90.6 MiB/s) |
| Baseline bootstrap (`canonical-bootstrap`) | 11m58s |
| ROS2/Kafka capture (`e2e-ros2-streaming`) | 50.2s |
| Kafka→MCAP (`e2e-streaming-capture`) | 1m28.0s |
| RobotRun registration (`e2e-robot-run-registration`) | 1m25.2s |
| Episode/learning build (`e2e-robot-run-learning`) | 1m15.0s |
| Full streaming E2E (smoke + 4 targets) | ~5m5s |
| Regression suite (lint + test + test-integration) | ~23s |
| **Full Phase 6.7 acceptance pass, end to end** | **~20 minutes** |

Historical Kafka rescanning (the run-scoped-consumer-group tradeoff documented in `streaming-transport.md` §26) was **negligible** here: each of the 3 capture-boundary E2Es ran against a topic freshly reset to zero history, and each captured its ~2,900-message run in well under 2 minutes total (replay + capture + registration + verification combined) — consistent with the "~3.4s for a lightly-used topic" data point in the Phase 6.6.1 addendum, not the "~44s at 150k prior messages" degraded case.

---

## 14. Known v1 limits (unchanged from the acceptance brief)

```text
new per-run consumer groups rescan Kafka history
local telemetry topic currently has one partition
practical Kafka payload ceiling is about 999 KB
scale measurement currently stops at 100k messages
hard SIGKILL temp cleanup relies on container-ephemeral storage
current validation environment is single-broker / single-machine
```

None of these were newly exposed or contradicted by this acceptance pass.

---

## 15. Final verdict

```text
PASS — Phase 6 streaming pipeline is clean-room accepted
```

## 16. Recommended next step after Phase 6

The streaming pipeline itself is accepted; the deferred, documented non-goals in `streaming-transport.md` §29 at the time of this report (renumbered §35 after Part 4 was added — RobotRun/Episode streaming lifecycle beyond `BuildEpisodesJobHandler`'s materialization, DLQ/retry policy, multi-partition scale) remain the natural Phase 7 candidates. Of these, the run-scoped-consumer-group Kafka-history-rescan cost (§26 of the same doc) is the most likely to become a real operational annoyance before the others, if per-RobotRun capture volume grows — worth benchmarking at realistic multi-run topic history before committing to a Phase 7 scope, rather than redesigning preemptively.
