# Canonical cutover acceptance (R5.1)

> Point-in-time record (2026-10-30). What was rebuilt, bootstrapped and verified after
> the SceneManifest v5 / EpisodeManifest v4 / unified `PayloadRef` cutover, on the local
> reference runtime and on disposable runtimes. The current contracts are in
> [reference-contract.md](../development/reference-contract.md),
> [reference-verification.md](../development/reference-verification.md),
> [ADR-016](../adr/016-logical-uri-and-verified-payload-access.md) and
> [ADR-017](../adr/017-unified-payload-ref-and-versioned-manifests.md).

## Environment

| Item | Value |
| --- | --- |
| Branch / HEAD | `refactor/canonical-data-architecture` @ `a216232` (cutover committed); working tree clean at start |
| Host | macOS (Darwin 24.3.0), Docker Desktop VM 94 GB (49 GB free at start, 30 GB after bootstrap) |
| Migration head | `a9d4e7b2c6f3` (repository head; no migration in the cutover) |
| Reference DB / bucket at start | `sceneops`: only `alembic_version`; bucket `sceneops`: 0 objects; `data/artifacts` empty |
| `uv lock --check` | passed (148 packages); the lockfile was not rewritten (`compose-build` was not used because it runs `uv lock`) |

Images built from the working tree with `docker compose build` (api, worker, publisher)
and by the bootstrap's own targets (acquisition, bridge, capture):

| Image | ID |
| --- | --- |
| `sceneops-platform/api:local` | `3acb88a6eaec` |
| `sceneops-platform/worker:local` (worker-pipeline, worker-jobs, disposable `test-worker-*`) | `3c76f695c1c4` |
| `sceneops-platform/publisher:local` | `a7fdb7089324` |
| `sceneops-platform/streaming-bridge:local` | `354dc754990b` |
| `sceneops-platform/capture:local` | `ea7e0ccc4819` |
| `sceneops-platform/dataset-acquisition:local` / `dataset-replay:local` | `f765b95ba6d3` / `ec6616ecfd4e` (cache-identical rebuild) |

The running api and worker containers were recreated on these images; the sha256 of
`sceneops_core/artifacts/schemas/payload.py` and `sceneops_core/scenes/schemas/manifests.py`
inside them equals the working tree's, `SceneManifest.schema_version` is
`Literal['sceneops.scene_manifest/v5']` and `ArtifactPayloadRef` is absent. `/health`,
`GET /api/v1/robot-runs` (database), Celery `ping` of both workers (queues
`sceneops.pipeline_runs`, `sceneops.jobs`) and MinIO `/minio/health/live` answered.

## Source data

| Check | Result |
| --- | --- |
| `make reference-data-verify REFERENCE_SCOPE=nuscenes-mini-full-10` | 10/10 fixtures equal `corpus.lock.json` (4,068,158,098 B, 93,373 messages); 10/10 L1-conformant (publisher image `a7fdb7089324`) |
| `make reference-source-verify SOURCE=lerobot-pusht` | verified at revision `7628202a…`, 7 files, 7,684,382 B |
| `make reference-verify-sources` | 56,732 checks, 0 violations, negative controls 6/6 |
| `make reference-expectations` (no `WRITE`) | the committed contract expectations are the derivation (22 checks, controls 2/2) |
| `make reference-verify-mixed-expectations` (no `WRITE`) | the committed mixed expectations are the derivation (7 checks) |
| Size and mtime of every file under `data/raw`, `data/reference`, `config/reference` (39,098 files) | identical before and after the whole session |

## Reference bootstrap

`make reference-contract-bootstrap REQUIRE_PRISTINE=1` from the empty environment:
06:32:43Z → 06:55:26Z (22 min 43 s, including the ROS 2 image builds). Recording Import
took about 6 minutes; Streaming Acquisition replayed each fixture through ROS 2 → bridge
→ Kafka → Capture (per-fixture message counts 8,897 / 9,206 / 9,476 / 9,400 / 9,446 /
9,323 / 9,535 / 9,493 / 9,290 / 9,307, each equal to the lock per topic, every capture
finalized by `RUN_END`).

The platform state converged (`state`: 20 contract RobotRuns, 20 Scenes, 20 Episodes,
pristine), but the command exited non-zero: its final evaluation runs the source-grounded
verifier, and the make target did not pass the MinIO endpoint and credentials
(`source_grounded: KeyError: 'MINIO_ROOT_USER'`). `reference-contract-verify` passes them.
Fixed in `makefiles/reference.mk` (both targets pass `$(REFERENCE_VERIFY_ENV)`), with a
regression test in `tools/reference/tests/test_reference_contract.py`.

| Run | Result |
| --- | --- |
| `make reference-contract-verify REQUIRE_PRISTINE=1` | ok; source-grounded 127,892 checks, 0 violations, controls 30/30; pristine |
| `make reference-contract-bootstrap REQUIRE_PRISTINE=1` after the fix (4 min 08 s) | ok; reused 20/20 RobotRuns and 40/40 units, created 0, changed 0; source-grounded as above |
| `make reference-verify DEEP=1` (every payload object read back and hashed) | ok; 140,446 checks, 0 violations, controls 30/30; modes recording_import + streaming_acquisition |

Two further convergence passes ran unintentionally during the fix (a `make -n` of the
bootstrap target executes it, because its recipe names `$(MAKE)`); both reused everything
and changed no record. The regression test reads the recipe as text for that reason.

Registered state:

| Record | Count |
| --- | --- |
| RobotRuns | 20 (10 `run-ref-…`, 10 `run-stream-ref-…`) |
| Scenes / Scene revisions | 20 / 20, all `sceneops.scene_manifest/v5` |
| Episodes / Episode revisions | 20 / 20, all `sceneops.episode_manifest/v4` |
| DatasetVersions | `sceneops-ref-nuscenes-mini-full-10/baseline` (10 + 10, `sha256:e09ec02c…`), `sceneops-stream-ref-nuscenes-mini-full-10/baseline` (10 + 10, `sha256:37cf186f…`) |
| Observation payload artifacts | 12,554 |
| Source contents | 0 (native sources are not part of the reference contract) |

## Payload access (reference environment)

A read-only probe in the worker container read every registered manifest through the
store, checked its checksum, loaded it with the strict v5 / v4 loader and collected its
`PayloadRef`s:

| Item | Result |
| --- | --- |
| Units / PayloadRefs | 40 / 17,238 (camera `image/jpeg` 9,368; lidar `application/x.ros2-cdr.sensor_msgs.msg.pointcloud2` 7,870); 0 selectors |
| Namespace and ownership | every URI `sceneops://observation-payloads/<own robot_run_id>/…`; no legacy key (`artifact_id`, `source_file`, …) in any manifest |
| Existence and size | 12,554 distinct URIs, each listed at its resolved physical URI with the pinned size; 0 missing, 0 mismatched |
| Verified reads | camera and lidar samples of both modes read through `VerifiedPayloadReader` (full sha256); each resolves to exactly the `uri` of its `observation_payload` artifact row; JPEG headers 1600×900 with EOI |
| Negative pins | wrong checksum and wrong size → `PayloadVerificationError`; missing key → `PayloadMissingError` |

Every payload object's full sha256 is additionally covered by `reference-verify DEEP=1`.

## GroundingDINO S3 image path on real MinIO

The inference server's FastAPI app and `ImageResolver` with a real boto3 client on the
local MinIO, model mocked (no weights, no inference-server image), host venv. Subject: a
Recording Import CAM_FRONT payload (119,917 B) of the reference bucket.

| Request | Result |
| --- | --- |
| correct pin | 200; the bytes `decode_image` received are the object `read_verified` returned; the model's image is pixel-equal to a decode of those bytes (1600×900) |
| wrong checksum; size − 1; size + 1 | 422 `image_integrity_mismatch`, model not called |
| missing key; missing bucket | 422 `image_not_found`, model not called |
| verified non-image (a lidar payload with its correct pin) | 422 `image_decode_failed`, model not called |

Not run: model inference, the inference-server image, `acceptance-grounding-dino`.

## Infrastructure suites (disposable runtimes)

Each ran through `disposable_env.py run --runtime` (database `sceneops_test`, bucket
`sceneops-test`, compose project `sceneops-test` on the images above), after its
snapshot capacity preflight.

| Suite | First run | Cause | After fix |
| --- | --- | --- | --- |
| `SUITE=nuscenes-scenes` | 8 passed, 1 failed | `test_camera_and_lidar_agree_with_the_robot_run_scene` read the removed payload shape (`payload.kind`, `sha256`) | 9 passed (cross-source: 224 CAM_FRONT checksums and 382 LIDAR_TOP timestamps equal; verifier 5,997 checks, 16 controls) |
| `SUITE=lerobot-episodes` | 10 passed | — | — (verifier 7,631 checks, 11 controls) |
| `SUITE=mixed-sources` | 12 passed, 1 failed | `test_the_selector_composes_the_same_version`: 209 × `manifest_forbidden_field` — the verifier matched the version name `selector` as a quoted token anywhere in the manifest text, and every v4 LeRobot PayloadRef now has a `"selector"` key | 13 passed (pins verify 8,608 checks, 14 controls; 4,699 native labels attached) |

The verifier now compares the DatasetVersion's names with the manifest's string values
only (keys are fixed by the schema); the `membership_in_manifest` control (the name
written into `sensor_id`) is still detected, and a regression test composes a version
named `selector` (`tools/reference/tests/test_mixed_source_verification.py`).

Mixed DatasetVersion facts, read from the disposable database before teardown (first run):
`pins`, `selector`, `concurrent` and `contested` each hold 1 recording Scene + 1 recording
Episode (`run-ref-nuscenes-mini-full-10-scene-0061`), 1 content Scene
(`content-1797603899be…`) and 206 content Episodes (`content-0322a8b9f1c6…`), all with
composition checksum `sha256:870b0a08…`. The probe on that runtime: 209 units (2 Scene v5,
207 Episode v4), 29,443 PayloadRefs, 3,570 distinct URIs all present at size under 3 owner
prefixes; content units reference only `sceneops://sources/contents/<own content>/files/…`
(no native payload republished under `observation-payloads`). Samples read through
`VerifiedPayloadReader` and compared with the raw files on the host — nuScenes camera,
lidar (`application/x.nuscenes.lidar-pcd-bin`), radar, and the pusht MP4
(`video/mp4`, 6,890,970 B, `ftyp isom`, 25,650 references each with its
`VideoFrameSelector`) — were byte-equal to `data/raw`. Only whole-object bytes were
verified; no video frame was decoded.

After all suites: the disposable database, bucket and containers no longer exist; the
reference environment's record fingerprint (`reference_contract.py fingerprint`), the row
count of every table and the MinIO object count and size are identical to before the
suites.

## Unit checks of the changed files

`pytest tools/reference/tests tools/checks/tests`: 249 passed. `make check-commands` and
`make check-boundaries` pass. `make test` (whole unit tier) was not rerun.

## Not run / limitations

- Model inference and `acceptance-grounding-dino`; the inference-server image was not built.
- `SUITE=sources`, `nuscenes-labels`, `pipelines`, `recovery`, `kafka`, `boundaries`; the
  Scene ML / Episode Learning journeys; `e2e-streaming-equivalence`; `e2e-cleanroom`.
- The native sources are verified only on disposable runtimes; the reference contract
  holds RobotRuns only.
- An empty volume `sceneops_kafka-data` was created by a probe before the streaming
  bootstrap and then used by the Kafka service.
