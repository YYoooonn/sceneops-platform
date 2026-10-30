# Downstream workflow acceptance (R5.2)

> Point-in-time record (2026-10-30 → 2026-10-31). The full regression, the remaining
> infrastructure suites, streaming equivalence and the Scene ML / Episode Learning
> journeys on the canonical v5 / v4 reference environment that
> [canonical-cutover-acceptance.md](./canonical-cutover-acceptance.md) built. Current
> contracts: [test-matrix.md](../development/test-matrix.md),
> [reference-contract.md](../development/reference-contract.md),
> [limitations.md](../architecture/limitations.md).

## A. Environment

| Item | Value |
| --- | --- |
| Branch / HEAD | `refactor/canonical-data-architecture` @ `a216232`; the R5.1 changes and this phase's are uncommitted |
| Reference environment at start | 20 RobotRuns, 20 Scene v5, 20 Episode v4, 2 baseline DatasetVersions, 12,794 artifacts, MinIO 24,460 files / 13,978 MiB; migration head `a9d4e7b2c6f3`; record fingerprint equal to the end of R5.1 |
| Docker VM / host free | 30 GB / 33 GB at start; 16 GB after the inference-server build; 28 GB / 29 GB at the end |
| Docker Compose | v5.5.1 |

Images (built from the working tree):

| Image | ID | Note |
| --- | --- | --- |
| `api:local` / `worker:local` / `publisher:local` | `3acb88a6eaec` / `3c76f695c1c4` / `a7fdb7089324` | unchanged since R5.1 |
| `streaming-bridge:local` / `capture:local` | `5ced1a8bb745` / `70a95690a98c` | cache-identical rebuild by `SUITE=kafka` |
| `dataset-acquisition:local` / `dataset-replay:local` | `25e0c121f4cf` / `5dbcd21c0a0e` | cache-identical rebuild by the journeys |
| `inference-server:local` (CPU) | `1fd6fe480e65`, 9.3 GB | built for the model acceptance (12 min; weights from the existing `cache/hf`, nothing downloaded), removed afterwards to make room for the LeRobot image |
| `lerobot-integration:local` | `361d922b86b3`, 7.11 GB | rebuilt (the previous image predated the cutover); a disk watchdog saw at least 17 GB free during the build |

## B. Test matrix

| Stage | Command | Count | Result | Environment | Real data | Model |
| --- | --- | --- | --- | --- | --- | --- |
| A | `make test` (includes `check-boundaries`) | 3,091 passed, 140 skipped (infrastructure-gated), 21 suites | passed | host | synthetic / committed fixtures | — |
| A | `make test-integration` | 387 passed (253 + 128 + 6), 0 skipped | passed | disposable DB + bucket | — | — |
| A | `tools/checks/source_dispatch.py`, `make check-commands` | — | passed | host | — | — |
| B | `SUITE=sources` | 6 | passed | disposable runtime | pusht + scene-0061 slice | — |
| B | `SUITE=nuscenes-labels` | 7 | passed | disposable runtime | scene-0061 slice | — |
| B | `SUITE=pipelines` | 25 | passed | disposable runtime | scene-0061 locked recording | — |
| B | `SUITE=recovery` | 24 | passed | disposable DB + bucket, own Redis / workers | — | — |
| B | `SUITE=boundaries` | runtime boundary 20 checks, acquisition 124, LeRobot adapter 25 | failed, then passed after a fix | host + one-shot containers | — | — |
| B | `SUITE=kafka` | bridge / capture / spanning 60 + 185 + 6 + 4 passed; transport smoke 0 / 7 | failed (smoke only) | reference broker | — | — |
| B | transport smoke on a fresh topic (`SCENEOPS_STREAMING_KAFKA_TELEMETRY_TOPIC=sceneops.smoke.r52-20261030`) | 39 | passed | reference broker | — | — |
| B | `SUITE=lerobot-episodes` (one test added) | 11 | passed after two corrections of the new test | disposable runtime | pusht | — |
| C | `make e2e-streaming-equivalence SCENE=<each of 10 fixtures>` | 8 checks × 10 | passed | reference (read-only) | all 20 contract RobotRuns | — |
| D | `make e2e-scene-ml` | 31 checks | passed | reference (`sceneops-test-scene-ml`) | scene-0061 | mock |
| D | `make acceptance-grounding-dino` | 31 checks | passed | reference (`sceneops-test-scene-ml-grounding-dino`) + CPU inference server | scene-0061 | GroundingDINO-tiny, real, CPU |
| E | `make e2e-episode-learning` | 25 checks | passed | reference (`sceneops-test-episode-learning`) + LeRobot container | scene-0061 | — |
| — | `make reference-contract-verify` (end) | 127,892 source-grounded checks, 30 / 30 controls | passed; 3 derived test datasets reported, not pristine (expected) | reference (read-only) | all | — |

## C. Canonical acceptance

- Schema versions: every reference unit is `sceneops.scene_manifest/v5` or
  `sceneops.episode_manifest/v4`; the unit tier and the disposable suites load nothing else.
- Recording and native registration: `sources` (preservation and registration of pusht
  and the scene-0061 slice, 3,002 objects / 527,820,150 B), `nuscenes-scenes` and
  `lerobot-episodes` (R5.1, repeated here), `pipelines` (concurrent registration, dedup,
  force, convergence).
- Native labels (`SUITE=nuscenes-labels`): one LabelSet of 4,699 labels over 39 anchors;
  CAM_FRONT views attach 4,699 labels over 224 samples and LIDAR_TOP views 4,699 over
  382; all 4,699 equal the locked reference labels of the RobotRun path; re-imports and
  a second snapshot write nothing; the label verifier detects 18 controls. Label anchors
  name the source by identity and the one anchor resolver matches identity — the
  `IDENTITY` site of the source-dispatch guard, which passes.
- Revision determinism: rebuilding, re-registering, re-composing and re-aligning
  converge on the same ids in every suite and journey that repeats a step; the 20 + 20
  contract revisions and both baseline memberships hash identically before and after
  every stage.
- Payload integrity: deep verification at the end of R5.1; in this phase the payloads
  were read through the journeys (camera images verified by the inference server, the
  lidar payload equal to the locked recording's message, alignment references equal to
  their pins — below).

## D. Downstream acceptance

### Streaming equivalence

For each of the 10 fixtures the two contract RobotRuns (Recording Import, Streaming
Acquisition): each pins its own recording (the import the locked file, the stream a
Kafka capture) with the locked counts; the same channels, types, encodings and
per-channel payload sequences; the same source observation times and `/tf_static`; equal
Scene and Episode *semantic content* per unit key — the manifest without the fields that
name the acquisition (RobotRun, recording artifact and checksum, producer fingerprint,
payload URI), so payload checksums, sizes and media types are compared and equal — while
the RobotRuns and producer fingerprints are required to differ. Unit ids therefore
differ by design (source-aware identity). Five negative controls are detected per
fixture; the platform fingerprint is unchanged.

### Scene ML

| Item | Mock backend | GroundingDINO (real, CPU) |
| --- | --- | --- |
| Labels / views | 4,699 labels over 39 samples; 221 samples, 3 anchors dropped (no lidar sweep within 25 ms) | same |
| Inference | 9 samples, 909 predictions | 9 samples, 77 predictions; lifting succeeded 65, not applicable 12, failed 0 |
| Evaluation (2 m) | TP 903, FP 6, FN 142; precision 0.993399 (903/909), recall 0.864115 (903/1045) | TP 36, FP 29, FN 1,009; precision 0.553846 (36/65), recall 0.034450 (36/1045) |
| Determinism | same run id → same prediction revision; re-evaluation → same primary metric | same |
| Lineage | prediction manifest pins the ScenarioSet and views; evaluation pins the prediction and label revisions; artifact ids derived from run + checksum | same |
| Lidar payload | the payload decodes to the locked recording's PointCloud2 message (34,688 points) | same |
| Scenes | revisions unchanged | unchanged |

The real-model run used the R2 contract end to end: the worker sent each camera
PayloadRef's physical URI with its checksum and size, and the server verified the bytes
before decoding. The mock metrics prove wiring, not model quality (the mock perturbs the
labels). Finding: the prediction manifest's `evaluable_prediction_count` is predictions
minus lifting failures (77); the evaluation summary's field of the same name counts the
localized predictions it scores (65 = TP + FP; 12 not localized because no lidar point
fell in the frustum). The metrics are consistent; the two fields share a name and differ
in meaning.

### Episode alignment and learning export

| Item | Result |
| --- | --- |
| Episode | scene-0061 Recording Import Episode, `sceneops.episode_manifest/v4`, 224 camera PayloadRefs |
| Alignment | 5 Hz on `vehicle.source_time`, `alignment_semantics_version` v3, 98 steps; the 98 camera reference values each equal the Episode's PayloadRef at the signal's source time (URI, checksum, size, media type); no artifact id in the aligned values |
| Retry | the same aligned revisions, export id and manifest record |
| Export | 98 steps, 784 signals; 98 reference rows with Logical URIs (`sceneops://observation-payloads/…`, 97 distinct objects), `reference_selector` null (whole JPEGs); shard checksums, row counts and `SceneOpsDataset` reads verified |
| LeRobot round trip | 1 episode, 98 frames at 5 fps read back by the official reader, every frame equal to the export's dense window; a re-run refuses the populated target |
| Episodes | revisions unchanged |

LeRobot: a real pusht Episode is still refused by alignment for its `next.*` transition
streams. The new `lerobot-episodes` test builds Episode 0 of the same registered snapshot
with the transition features left out of the build configuration, composes and aligns
it (10 Hz, 50 ms tolerance): 161 steps, 161 reference values, each equal to the Episode's
PayloadRef of its frame — one MP4 object (6,890,970 B) and a distinct `video_frame`
selector per step. No frame is decoded; decoding and image tensors are not implemented.
The learning and LeRobot exports were not run on a LeRobot Episode.

## Cross-domain processing (Stage F)

The source-dispatch guard passes: downstream of the manifests no code compares a source
kind, format or payload namespace. A search of the derived, inference, evaluation,
alignment, sample-view and export modules for RobotRun / content identity found only test
helpers. Observed refusals are by capability: sample views skip a Scene without the
anchor channel (`scene_lacks_anchor_channel`), alignment refuses transition streams,
detection refuses selected payloads (`require_whole_payloads`); the analytics export
takes recording and content Scenes alike. Source-aware code stays in registration, source
import, label import and the one anchor resolver. One projection gap: the analytical
`scenes` table carries `robot_run_id` and no content identity, so a content Scene's row
names no source.

## Fixes in this phase

| Failure | Class | Fix | Regression |
| --- | --- | --- | --- |
| `check-runtime-boundary`: 16 failures, each the probe output `Container … Creating / Created  done` | environment / tooling: Compose v5.5.1 writes lifecycle progress to stderr on `run`; the probe merges stderr and accepts exactly one line `done` (every probe had printed only `done`: no boundary was crossed) | `--progress quiet` in the script's Compose invocation | `tools/checks/tests/test_runtime_source_boundary.py` |
| new LeRobot alignment test: `nearest` without `tolerance_us`; aligned artifact read without its envelope; pins keyed by (topic, uri) collapsed every frame of the one MP4 to the last selector | test defects (mine) | explicit tolerance; `AlignedEpisodeArtifact`; pins keyed by frame time | the test itself |

The transport smoke on the default topic was not changed: it fails once the topic holds
more than a few GB (here 5.0 GB from the ten streamed fixtures), a fresh consumer group
re-reading it through the host port forward within a fixed 30 s window; on a fresh topic
it passes 39 / 39.

## State audit

After each stage: the golden fingerprint (`reference_contract.py fingerprint --golden`),
the contract's 20 + 20 revisions and the two baseline memberships, and the size and
mtime of every file under `data/raw`, `data/reference`, `config/reference` were
unchanged; the disposable suites left no database, bucket or container. Stages A–C left
the reference environment row-for-row unchanged. Stages D–E added only their documented
derived state: Datasets `sceneops-test-scene-ml`, `sceneops-test-scene-ml-grounding-dino`,
`sceneops-test-episode-learning` (each one DatasetVersion pinning the existing scene-0061
Scene and Episode revisions: +6 members, no new Scene or Episode revision), 2 label sets, sample views, 2 ScenarioSets, 2 inference and 4 evaluation runs, 2 models, the aligned and
export artifacts (+29 artifact rows, +86 MinIO files, +7 MiB), and their Jobs and
PipelineRuns. Kafka holds one extra topic (`sceneops.smoke.r52-20261030`).

## E. Remaining gaps

| Kind | Item |
| --- | --- |
| Confirmed defect (minor, pre-existing) | `evaluable_prediction_count` means two different counts in the prediction manifest and the evaluation summary |
| Known unsupported | video frame decoding / image tensors for selected MP4 references; alignment of Episodes with transition streams (all real pusht Episodes) |
| Not run | the learning and LeRobot exports on a LeRobot Episode; `e2e-cleanroom`; GPU inference; Scene ML on a native nuScenes Scene (covered only by the `nuscenes-scenes` / `nuscenes-labels` suites' views and mock detection) |
| Environment | the transport smoke's 30 s window on a multi-GB default topic |
| Optional improvement | content source identity in the analytical `scenes` table; `learning_signals` rows carry `reference_uri` and `reference_selector` but not the pin's checksum / size / media type (recoverable from the pinned aligned revision) |
