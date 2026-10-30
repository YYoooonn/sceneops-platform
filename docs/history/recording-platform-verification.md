# Recording Import platform verification (Step 7B-1)

> Point-in-time record. It describes what was run and observed at the commit below; it is
> not a current contract. Current rules:
> [reference-verification.md](../development/reference-verification.md); contract:
> [reference-contract.md](../development/reference-contract.md); decision:
> [ADR-015](../adr/015-multi-source-platform.md) §12. Preceded by
> [reference-verification-foundation.md](./reference-verification-foundation.md) (raw-source
> verifier, no platform output).

```text
date         2026-10-19
branch       refactor/multi-source-platform
HEAD         9f0c8f6 (working tree: this change, uncommitted)
host         macOS (Darwin 24.3.0), repository venv, Python 3.11.11; Docker Desktop VM
             filesystem 58.4 GB
images       api / worker / publisher / dataset-acquisition rebuilt from the clean tree at
             9f0c8f6 (api 04bde89be407, worker a65c8187fb14) before the run
inputs       data/raw/nuscenes (v1.0-mini), data/reference/nuscenes-mini-v1 (10 locked
             MCAPs, 4.07 GB), config/reference/nuscenes-mini-v1/corpus.lock.json
scope        Recording Import only: 10 fixtures, 10 RobotRuns, 10 Scenes, 10 Episodes,
             DatasetVersion sceneops-ref-nuscenes-mini-full-10/baseline
not run      Streaming Acquisition, Recovery, the learning / L3 journeys, cleanroom
```

## 1. Isolation

The platform output was produced on a runtime of its own, built from the repository's
disposable-test infrastructure (`tests/infrastructure/disposable_env.py`,
`execution_runtime.py`), driven from a session script so the runtime stayed up between
bootstrap and verification:

| Resource | Used | Reference environment |
| --- | --- | --- |
| PostgreSQL database | `sceneops_test_ref7b`, migrated to head (`a3f7c1e9d5b8`) | `sceneops`: not read or written by the platform |
| MinIO bucket | `sceneops-test-ref7b` (`SCENEOPS_WORKER_ARTIFACT__ROOT_URI` for API, workers and the compose-run publisher) | `sceneops`: not written |
| API, Celery workers, Redis | compose project `sceneops-test` (`compose/test-runtime.yaml`); settings read back from the running processes (`verify_isolation`) | not used |
| Shared | the PostgreSQL and MinIO *servers* and the Docker VM disk | |

A read-only fingerprint of the reference environment (per table: row count and md5 of the
ordered rows; the bucket listing's keys, sizes and ETags) was taken before the run and
compared after the PostgreSQL recovery below and after teardown: identical each time. At
the start the reference environment was freshly reset (26 tables, only `alembic_version`
at head; empty bucket). The disposable database, bucket and runtime were dropped at the end.

## 2. Procedure

| Step | Command | Result | Wall time |
| --- | --- | --- | --- |
| raw sources | `make reference-verify-sources` (10 fixtures, every source file hashed) | 56,732 checks, 0 violations, controls 6 / 6 | 37 s |
| expectations | `make reference-expectations` | 22 checks, 0 violations, controls 2 / 2 | 14 s |
| synthetic | `make reference-verify-selftest` | 89,998 checks, 0 violations, controls 25 / 25 | 24 s |
| bootstrap | `tools/baselines/canonical/canonical_bootstrap.sh`, `REFERENCE_SCOPE=nuscenes-mini-full-10`, the runtime's `API_BASE_URL` and artifact root | 10 RobotRuns registered; per run `recording_scene_building` and `recording_episode_building` succeeded; `COMPOSE_DATASET_VERSION` created `baseline`, 20 members, `composition_checksum sha256:70a5e41e…0608`; `baseline_verify` passed | 5 min 44 s |
| platform | `reference_verify.py verify --deep --mode recording_import` (first run) | 44,996 checks, **10 violations** (§3); controls not run | 2 min 05 s |
| platform | the same, after the rule correction | 44,996 checks, **0 violations**, controls **25 / 25** on the collected observation | 1 min 55 s |
| re-check | `reference-verify-sources`, `reference-expectations`, `reference-verify-selftest` after the correction | 56,732 / 22 / 89,998 checks, 0 violations, 6 / 6, 2 / 2, 25 / 25 | |

The expectations were derived from the locked recordings before any platform output
existed, and were never derived from or compared against an earlier golden output.

## 3. Finding: the `ros2_message` media type rule

All 10 violations were one code and one cause:

```text
scene_observations  /lidar/top/points observation 0 (every fixture)
observed  [t, sha256:4638f9…, 693929, application/x.ros2-cdr.sensor_msgs.msg.pointcloud2, None]
expected  [t, sha256:4638f9…, 693929, application/x.ros2-cdr.sensor_msgs.msg.PointCloud2, None]
```

Timestamp, checksum and size agreed; only the media type's case differed.

Triage. The accepted decision, [ADR-007](../adr/007-canonical-ingestion-architecture.md)
§30.4, states `application/x.ros2-cdr.<package>.msg.<type, lowercased>`, e.g.
`…sensor_msgs.msg.pointcloud2`; the platform's `validate_media_type` admits only
lowercase media types, and the derived-layer consumer dispatches on the lowercase name.
The platform follows the accepted rule. The verifier had taken its rule from
`scene-domain.md`, which wrote `<package>.msg.<type>`, "e.g. PointCloud2", and the 7A
unit test pinned that spelling. **Classification: imprecise active document and a verifier
rule defect, not a platform defect.** No accepted rule was changed.

Correction:

- `tools/reference/verification/expectations.py`: the type is lowercased (ADR-007 §30.4;
  ROS 2 package names are lowercase already, REP 144).
- `tools/reference/tests/test_reference_verification.py`: the rule test now expects the
  ADR's own example, `application/x.ros2-cdr.sensor_msgs.msg.pointcloud2`.
- `docs/architecture/scene-domain.md` and `docs/development/reference-verification.md`
  state the lowercased form.
- `reference_expectations.json` re-derived with `derive --write` from the locked
  recordings: exactly the 10 `/lidar/top/points` `observations_digest` values changed
  (one per fixture); counts, unit keys, unit ids and windows are unchanged.

## 4. Coverage of the passing run

| Subject | Verified |
| --- | --- |
| RobotRuns | 10, under `robot-ref-nuscenes-mini-full-10`; recording artifact `art-robotrun-<run>`; every recording read back (4,068,158,098 bytes) and hashed equal to the record and to the locked sha256 |
| Unit identities, segmentation | per run exactly the derived Scene id (`whole_recording-233eebe491d93840/recording`) and Episode id (`event_markers-514c31ea4e90ecfa/task-mission-<scene>-000`), each id recomputed from its binding; one revision each; windows and projection columns equal to the derivation |
| Manifests | 20; bytes read back (15.96 MB), checksum = revision = ArtifactRecord, size, canonical JSON, schema version, no forbidden field; `manifest_artifact_id` recomputed from unit id and checksum |
| Scene content | per fixture 606–640 observations (front camera + lidar) in canonical order with payload checksum, size, media type and image size; 2,963–3,197 poses; calibrations, frames, channels; no groups |
| Episode content | streams, every occurrence (states, actions, events, camera observations) with decoded values of the same JSON type |
| Lineage, RevisionPins | `source_kind`, `robot_run_id`, `recording_artifact_id`, `recording_checksum`, window, `unit_key`; producer fingerprint recomputed from producer, build config and source revision; build config carries the reference configuration; Scene producer identity |
| Payloads (deep) | 6,277 `observation_payload` artifacts (3,935 `…pointcloud2`, 2,342 `image/jpeg`), 3,156,445,716 bytes, **every object read back and hashed**; record = reference (checksum, size, media type), owned by its RobotRun; one artifact per recorded message and extraction |
| Shared artifacts | the Episodes reference 2,342 camera payloads, all of them the Scenes' artifacts (no second artifact for any message); lidar is Scene-only in the reference build configurations, so sharing is evidenced for the camera channel only |
| DatasetVersion | `sceneops-ref-nuscenes-mini-full-10/baseline`: members exactly the 20 derived unit ids, one per unit; every pin a registered revision with a recomputed artifact id; `composition_checksum` recomputed from the pins; summaries `scene_count 10`, `observation_count 6,277`, `keyframe_count 0`, two observed channels, `episode_count 10` equal to the derivation |
| Negative controls | all 25 platform controls detected on copies of the real observation (scene-0061); with Recording Import alone, a binding is moved to scene-0103's RobotRun and the foreign member is a fabricated non-contract unit |

## 5. Tooling change for a single-mode run

`reference_verify.py verify --mode <mode>` (`make reference-verify MODE=…`) verifies one
ingestion mode; without it every mode is required, as `reference-contract-verify` does.
The report names its `modes`. The platform controls' context (`control_context`) moves a
binding to another fixture's run and fabricates a foreign unit when no observed run lies
outside the version. 27 tests were added (a Recording-only state passes and is not the
whole contract; every control is detected in that scope); `tools/reference/tests`: 124 passed.

## 6. Incident: Docker VM disk exhausted

The first bootstrap attempt filled the Docker VM's filesystem (58.4 GB, 3.1 GB free after
the image rebuild) while publishing the ninth recording. PostgreSQL, shared by the
disposable and the reference databases, hit `No space left on device` at a checkpoint,
PANICked, and its crash recovery failed for the same reason; the registration request got
a 500. Recovery: the disposable runtime and bucket were dropped, `docker start
sceneops-postgres-1` replayed the WAL, and the reference fingerprint was compared
(identical). The user then had `sceneops-platform/inference-server:local` and
`lerobot-integration:local` removed by name (rebuild before the grounding_dino and LeRobot
journeys), giving 13.5 GB; the second run went down to 6.4 GB free and ran with a watchdog that
would have stopped the runtime below 2 GB. The full Recording Import baseline occupies
about 7.2 GB of MinIO. Dropped objects stayed in MinIO's `.minio.sys/tmp` (6.8 GB) for a
few minutes before they were purged. The current constraint is stated in
[local-development.md](../development/local-development.md#disk-hygiene).

## 7. Limitations and remaining requirements

- **Measured scope.** One run of the 10-fixture corpus on one machine; the verifier read
  the state once. The bootstrap is create-or-verify, but its convergence on a second run
  was not exercised here.
- **Not in the source rules** (unchanged from 7A): payload artifact id formula,
  observation / occurrence / pose / calibration id spelling, the full normalized
  `build_config`, CAN pose / battery values against the CAN extract, clock origins,
  `fixed_duration`, content sources.
- **Streaming Acquisition (7B-2).** Needs Kafka, the bridge, Capture and the replay images
  on the same isolated pattern, the 10 streamed RobotRuns and their DatasetVersion; then
  `verify` with both modes, where `streaming_content` re-derives each captured recording.
  Disk: a streamed fixture adds ~0.47 GB of Kafka log on top of its MinIO share, so the
  two modes together need well over the 13.4 GB now free; the streaming bootstrap's
  `MIN_FREE_GIB` guard applies, the canonical bootstrap has none.
- **Recovery.** No verifier rule covers it; a recovered RobotRun would be held to the same
  content checks. `SUITE=recovery` and the lifecycle acceptance were not run.
- **Cleanroom.** `e2e-cleanroom` resets the reference environment and runs
  `reference-contract-verify`, which needs both modes; not run, and it is destructive by
  design, so it needs explicit authorization.
- **Follow-up, not fixed:** the canonical bootstrap has no free-space check before
  publishing, and exhausting the shared VM disk stops PostgreSQL for every database.
