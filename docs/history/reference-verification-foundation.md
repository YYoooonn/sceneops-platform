# Reference verification foundation (raw-source verifier, Step 7A)

> Point-in-time record. It describes what was audited, built and measured at the commit
> below; it is not a current contract. Current rules:
> [reference-verification.md](../development/reference-verification.md); contract:
> [reference-contract.md](../development/reference-contract.md); decision:
> [ADR-015](../adr/015-multi-source-platform.md) §12.

```text
date         2026-10-19
branch       refactor/multi-source-platform
HEAD         1bad1ac (working tree: this change, uncommitted)
host         macOS (Darwin 24.3.0), repository venv, Python 3.11.11
inputs       data/raw/nuscenes (v1.0-mini), data/reference/nuscenes-mini-v1 (10 locked
             MCAPs, 10 label artifacts), config/reference/nuscenes-mini-v1/corpus.lock.json
platform     not running (only the test PostgreSQL / MinIO containers); the reference
             database still holds pre-refactor state and was not reset
```

## 1. Audit of the previous reference expectations

| Assumption | Where | Outcome |
| --- | --- | --- |
| Scene / Episode rows carry `datasetId` / `datasetVersion`; membership read from them | `reference_contract.py` (`_contract_units`, `inventory`), its tests | **replaced**: membership is what a DatasetVersion pins (`/datasets/{id}/versions/{v}/{scenes,episodes}`) |
| `updatedAt` / one `manifestChecksum` per unit row as the change signal | `snapshot`, `platform_fingerprint`, `golden_fingerprint` | **replaced**: unit rows with every revision's checksum and registration time (units and revisions are never updated) |
| `units.scene_policy` = `whole_recording`, `units.scene_unit_key` = `recording` stated in the contract | `reference_contract.json` v1, `scene_shape` check | **removed**: unit keys are `<policy>-<digest>/<segment>` and are derived, not stated |
| A per-RobotRun count of units is enough | `scene_shape` / `episode_shape` | **replaced**: the exact derived unit ids (`unit_id` v2), one revision each (`revision_count`) |
| Readiness "at the current revision" | `reference-contract.md` | **replaced**: at the revision the DatasetVersion pins (the baseline verifier already did this) |
| Contract content accepted through the platform's own verifiers (`canonical-verify`, `streaming-verify`, manifest facts) | `reference-contract-verify` | **retained** as operational checks; acceptance of content added through the source-grounded verifier (`source_grounded`) |
| Streaming / import equivalence by comparing two platform outputs (`semantic_scene_content`, production code) | `tools/e2e/streaming_equivalence_verify.py` | **retained** as transport-equivalence evidence; not independent ground truth. The new verifier requires each capture's re-derived content to equal the locked recording's (`streaming_content`) |
| RobotRun / Robot / Dataset identities (`run-ref-nuscenes-mini-full-10-<fixture>`, …, version `baseline`) | `reference_contract.json` | **retained**: they are declared acquisition / composition inputs, not derived expectations; the reference runtime is rebuilt, not migrated |
| `sceneops.reference_contract/1` | contract file and tool | **replaced** by `/2` (`build_configs`, `expectations`, `units.revisions_per_unit`) |
| Aligned-episode artifacts carry `datasetId` / `datasetVersion` | `tools/e2e/e2e_episode_learning.sh` | **unchanged**: alignment is deliberately DatasetVersion-scoped (Episode domain §5); outside this verifier |

Two identity rules were stated less precisely in the active documents than the code
implements them and were made exact there: the boundary document's
`recording_segmentation_schema` member (ingress-boundaries §6) and the zero-padded,
0-based `task-<key>-<NNN>` Episode segment (episode-domain §3). As a diagnostic outside
the verifier, the formulas as implemented in `tools/reference/verification/rules.py`
produced the same Scene / Episode unit keys, unit id and manifest artifact id as the
production functions for `scene-0061`.

## 2. Results

| Command | Scope | Checks | Violations | Negative controls | Wall time |
| --- | --- | --- | --- | --- | --- |
| `reference_verify.py sources` | 10 fixtures, every source file hashed | 56,732 | 0 | 6 / 6 detected (`scene-0061`) | 30 s |
| `reference_verify.py derive --write`, then `derive` | 10 fixtures | 22 | 0 | 2 / 2 detected | — |
| `reference_verify.py selftest` | 20 RobotRuns, synthetic platform state | 89,998 | 0 | 25 / 25 detected (`scene-0061`) | 25 s |

`selftest` proves that the checks and controls work on a state the documented rules
describe. **It is not evidence about the platform**: no platform output was verified.

Source facts verified per fixture: per-channel message counts and header stamps of six
cameras and `LIDAR_TOP` against `sample_data` (key frames and sweeps), byte equality of
every image and point cloud with its source file, CameraInfo size and intrinsics, every
`/tf` ego pose (radar `sample_data` included, shared timestamps of `scene-1094`
included), `/tf_static` calibration, CAN counts and times, control values, the two
mission events, `log_time` / `publish_time` timing, the lock, and every label (anchor,
token, instance, category, attributes, box) against `sample_annotation`.

Derived per fixture (committed in `reference_expectations.json`): one Scene,
`whole_recording-233eebe491d93840/recording`, 606–640 observations (front camera +
lidar), 2,963–3,197 poses; one Episode, `event_markers-514c31ea4e90ecfa/task-mission-<scene>-000`;
the unit ids of both modes.

## 3. Gaps and remaining E2E requirements

- **Platform output is unverified.** `make reference-verify` (inside
  `reference-contract-verify`) has never run against a platform; the collector is
  tested against an in-memory fake of FastAPI and the store only. Its first run against
  the regenerated contract is the acceptance (ADR-015 §12 step 1–2); a disagreement is
  triaged as a defect or a document gap, never fitted.
- **Recording Import.** Needs a reset reference runtime, rebuilt images, the contract
  bootstrap's Recording Import half, then `reference-verify` (and once `DEEP=1`).
- **Streaming Acquisition.** Needs the streamed half of the bootstrap; `streaming_content`
  then compares each capture's re-derived content with the locked recording. Not run.
- **Recovery.** No verifier rule covers recovery: a recovered RobotRun (publish-pending,
  reconcile) is held to the same content checks, but the recovery workflows themselves
  stay with `SUITE=recovery` and the lifecycle acceptance, which this work did not run.
- **Cleanroom.** `e2e-cleanroom` reaches the verifier through `reference-contract-verify`;
  not run. Its runtime now includes reading every contract recording back (~7 GB).
- **Not covered by rules yet:** content sources (SourceContent / SourceSnapshot /
  `ContentUnitSource` / `SourceFileRef`), clock-domain origins, `fixed_duration`
  segmentation, payload artifact id formula, derived resources.
