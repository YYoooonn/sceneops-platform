# Source-grounded reference verification

> Developer / test verification, not a Pipeline. Decision record:
> [ADR-015](../adr/015-multi-source-platform.md) §12 (R5). Inputs: the
> [reference corpus](./reference-corpus.md) and the
> [golden reference contract](./reference-contract.md).

The golden reference contract is accepted only through this verification. Its
expectations are derived from preserved raw inputs by the documented rules of the
canonical model, implemented a second time in `tools/reference/verification/`.
Platform output is never its own reference: the verifier reads what the platform
holds and compares it with what the raw inputs determine.

```text
raw nuScenes tables + files ──┐
                              ├─ sources ──▶ the locked recording is the raw scene
locked MCAP (corpus.lock) ────┤
                              └─ derive ───▶ reference_expectations.json
                                               unit keys, unit ids, windows,
                                               per-channel / per-stream digests
platform (FastAPI + store) ───── verify ───▶ RobotRuns, units, revisions, manifests,
                                               payloads, DatasetVersions
                                               against the derivation
every layer ──────────────────── negative controls must fail
```

## Independence

- The verifier imports no SceneOps package. It reads MCAP with its own record reader
  (`mcap_records.py`, from the MCAP specification) and decodes ROS 2 CDR with its own
  decoder (`cdr.py`), so it shares no reader with the recording builders. It uses
  `zstandard` / `lz4` to decompress chunks and `boto3` to read the object store.
- Identity and checksum formulas (`rules.py`) are implemented from the documents named
  below. A disagreement with the platform is a finding: an implementation defect or an
  imprecise document. Neither the rules nor the expectations are adjusted to make the
  platform pass.
- `reference_expectations.json` is written only by `reference_verify.py derive --write`
  from the locked recordings and the build configurations. Every verification derives
  it again and fails on any difference (`expectations_drift`).

## Rules

### Sources (raw nuScenes → locked recording)

The source preparation's mapping is documented in the
[dataset-acquisition README](../../integrations/dataset-acquisition/README.md)
("nuScenes mapping", "Timing"). For each fixture's scene:

| Fact | Expected from the raw scene |
| --- | --- |
| camera / lidar messages | one per `sample_data` of the channel whose sample belongs to the scene (key frames and sweeps) |
| header stamps | `timestamp_us × 1000` |
| payload bytes | `CompressedImage.data` / `PointCloud2.data` = the bytes of the `sample_data` file (sha256) |
| `camera_info` | one per image, same stamp; size = `sample_data` width / height; `K` = `camera_intrinsic`; zero distortion; `P = [K \| 0]` |
| `/tf` | one `map → base_link` per `sample_data` of the scene, every sensor including radar, equal to its `ego_pose` |
| `/tf_static` | `base_link → <sensor frame>` = the one `calibrated_sensor` of each converted sensor |
| CAN | `/vehicle/odom` = `pose`, `/vehicle/imu` = `ms_imu`, `/vehicle/status` and `/vehicle/control` = `vehicle_monitor`; one message per entry at `utime × 1000`; control values = steering / throttle / brake |
| mission | `running` at the first and `completed` at the last source time of the recording, `mission_id = mission-<scene>` |
| timing | `log_time = publish_time`; a stamped message's `log_time` = its stamp |
| lock | recording sha256, size, message and per-topic counts = the cached recording's bytes |
| labels | one sample per scene sample in sample order, anchored at its `LIDAR_TOP` key frame; one label per `sample_annotation` (token, instance, category, attributes, translation, size, rotation); artifact sha256, size and counts = the lock's |

### Canonical units (locked recording + build configuration → expectations)

Rules from [Scene domain](../architecture/scene-domain.md) §1 and §6,
[Episode domain](../architecture/episode-domain.md) §2–§4,
[Ingress boundaries](../architecture/ingress-boundaries.md) §4 and §6 and
[ADR-007](../adr/007-canonical-ingestion-architecture.md) §8.3, §15.2 and §30.4–§30.6.

| Subject | Rule |
| --- | --- |
| unit key | `<policy>-<sha256(boundary document)[:16]>/<segment>`; the boundary document is the segmentation configuration with `recording_segmentation_schema` |
| Scene segment | `whole_recording`: one unit, segment `recording`, window `[earliest, latest + 1)` over every included observation and pose on the segmentation clock |
| Episode segments | `event_markers`: markers in canonical order; a start opens a task key and an end closes it; window `[start, end + 1)`; segment `task-<key>-<NNN>`, `NNN` = 0-based count of that key's earlier windows |
| unit id | `<domain>-<sha256(canonical_json({unit_id_schema: "sceneops.unit_id/v2", domain, source_kind: "recording", source_id: robot_run_id, unit_key}))[:32]>` |
| Scene observations | one per message of each configured channel; timestamp = header stamp; canonical order timestamp, MCAP sequence, file order; payload `compressed_image` = sha256 / size of `CompressedImage.data` (`image/jpeg`), `ros2_message` = sha256 / size of the recorded CDR message (`application/x.ros2-cdr.<pkg>.msg.<type, lowercased>`, ADR-007 §30.4); `image_size` = CameraInfo size; no ego pose association |
| Scene calibration / frames / poses | extrinsic = static `ego → channel frame`; intrinsic = CameraInfo `K`; frames = ego, world, channel frames (sensor); poses = every configured `parent → child` transform at its own stamp; no keyframe groups |
| Episode streams | topic, role, recorded schema name, clock, fields, `has_payload` |
| Episode occurrences | every message whose time (header stamp or payload field) lies in the window; values = the selected field paths as decoded (an integer stays an integer); observation payloads as for Scenes |
| lineage | `source_kind = recording`, `robot_run_id`, `recording_artifact_id = art-robotrun-<run>`, `recording_checksum`, window, `unit_key` |
| producer | fingerprint = `sha256(canonical_json({fingerprint_schema, producer_id, semantics_version, build_config, source: {source_kind, robot_run_id, recording_checksum}}))`; `build_config` carries every member of the reference build configuration; Scene producer `sceneops.recording_scene_builder` v3 |

### Platform state (observation → checks)

| Subject | Check |
| --- | --- |
| RobotRun | registered under the contract robot; recording artifact `art-robotrun-<run>` of kind `robot_run_recording`; the stored object's bytes equal the record; Recording Import pins the locked sha256; Streaming Acquisition pins its capture, whose per-topic counts equal the lock's and whose derived canonical content equals the locked recording's |
| units | the run's Scenes / Episodes are exactly the derived unit ids; each row's id recomputes from its binding; exactly `revisions_per_unit` revisions; one checksum per producer fingerprint |
| revision | `manifest_artifact_id = f(unit_id, manifest_checksum)`; window and projection columns equal the derivation |
| manifest | record kind, checksum and size equal the stored bytes; the bytes are canonical JSON; no DatasetVersion, unit id, status, location or execution field; schema version; lineage and producer as above; content equal to the derivation |
| payloads | every reference resolves to an `observation_payload` record with exactly its checksum, size and media type, owned by the RobotRun; the object exists with that size (`--deep`: those bytes); one artifact per recorded message and extraction, shared by the Scene and the Episode |
| DatasetVersion | members are exactly the mode's derived unit ids, one per unit; each pin names a registered revision and its artifact id recomputes; `composition_checksum` recomputed from the pins; summaries (`scene_count`, `observation_count`, `keyframe_count`, `observed_channels`, `episode_count`) equal the derivation |

## Negative controls

Every command runs its controls and fails when one is not detected. A *consistent*
fault rewrites everything the platform derives from the corrupted value (canonical
re-serialization, manifest checksum and artifact id, revision row, member pins,
composition checksum), so only the independent expectations can catch it.

| Layer | Controls (code that must appear) |
| --- | --- |
| sources | dropped `/tf` message (`source_message_count`), 1 ns camera stamp shift (`source_timestamp`), changed point-cloud byte (`source_payload_bytes`), re-locked recording (`lock_recording_checksum`), 1 ns label anchor shift (`source_label_anchor`), dropped label (`source_label_content`) |
| expectations | committed Scene window off by 1 ns, expectations from another build configuration (`expectations_drift`) |
| platform | dropped observation, 1 ns shift, payload bytes rebound consistently (`scene_observations`); changed stored payload byte (`payload_object_checksum`); member pins an unregistered revision (`member_pin`); member artifact id of another revision (`pin_artifact_id`); Episode re-based to unit-relative time on an absolute clock (`window`, `episode_occurrences`); absolute anchor added to poses (`scene_poses`, `window`); binding to another recording or RobotRun with a recomputed fingerprint (`source_binding`); another unit key (`unit_identity`); missing or foreign member (`composition_members`); wrong `composition_checksum`; wrong summary (`composition_summary`); changed action value, dropped end marker (`episode_occurrences`); build config changed without fingerprint (`producer_fingerprint`); pretty-printed manifest (`manifest_canonical`); `dataset_version` in a manifest (`manifest_forbidden_field`); missing payload or manifest object; substituted recording (`robot_run_recording`); second revision (`revision_count`); one message through two payload artifacts (`payload_identity`) |

`verify` runs the platform controls on a copy of the collected observation, only when
that observation passes.

## Commands

```text
make reference-verify-sources [FIXTURE=<id>]   raw nuScenes <-> recordings <-> lock <-> labels   no stack
make reference-expectations [WRITE=1]          derive and compare (WRITE=1: replace the file)   no stack
make reference-verify-selftest                 synthetic state passes, every control detected   no stack
make reference-verify [DEEP=1] [MODE=<mode>] [FIXTURE=<id>]   the platform's contract state, read-only
make reference-contract-verify                 the contract state check; runs reference-verify
```

`MODE=recording_import` (or `streaming_acquisition`) verifies one ingestion mode: its
RobotRuns, units and DatasetVersion only, with the platform controls applied in that
scope (a binding is moved to another fixture's run, a foreign member is a unit outside
the contract). `FIXTURE=<id>` (`verify --fixture`) verifies one fixture's RobotRuns and
units and the DatasetVersion the baseline bootstraps compose for that selection,
`<dataset>/baseline-<id>` ([canonical-baseline.md](./canonical-baseline.md)), held to
exactly that fixture's units and summaries; the controls then run on that fixture, and
the two that need a second member of one domain use the version's other unit. The
report lists the `modes`, `fixtures` and `dataset_versions` it covers; a report of one
mode or one fixture is evidence about that scope, not the contract's acceptance.

### A streamed fixture on a disposable runtime

The streaming path can be verified without the reference environment: the disposable
execution runtime with its own streaming services (`disposable_env.py run --streaming`,
[test-matrix.md](./test-matrix.md#the-disposable-execution-runtime)) runs the streaming
bootstrap under the contract's identity and the verifier against it, and drops
everything afterwards:

```text
POSTGRES_USER=… POSTGRES_PASSWORD=… MINIO_ENDPOINT_URL=http://localhost:9000 \
MINIO_ROOT_USER=… MINIO_ROOT_PASSWORD=… \
uv run python tests/infrastructure/disposable_env.py run --streaming \
    --database sceneops_test_<name> --bucket sceneops-test-<name> -- sh -c '
  FIXTURE=scene-0061 tools/baselines/streaming/streaming_bootstrap.sh &&
  uv run --no-sync python -I tools/reference/reference_verify.py verify --deep \
      --mode streaming_acquisition --fixture scene-0061'
```

Build the images first (`make acquisition-image`, `make streaming-ros-build`); the
bootstrap's capacity preflight refuses a selection the Docker VM cannot hold
([local-development.md](./local-development.md#disk-hygiene)).

`sources` needs `data/raw/nuscenes` (`NUSCENES_DATAROOT`) and the prepared corpus;
the others need the prepared corpus only. Each prints one JSON report
(`sceneops.reference_verification_report/1`) with per-code check counts, violations
and negative-control results, and exits non-zero on any violation or undetected
control. `selftest` proves the checks on a synthetic state and says nothing about the
platform.

## Coverage and limits

- **Recording sources only.** No content-sourced builder exists yet, so SourceContent /
  SourceSnapshot identity, `ContentUnitSource` bindings, `SourceFileRef` payloads and
  MS-14 execution provenance have no rules here.
- **Clock domains.** Manifests declare clock identifiers, not origins (ADR-015 §6.2 is
  not implemented). The controls show that re-basing to unit-relative time or adding an
  absolute anchor is detected through timestamps and windows; an origin declaration
  itself cannot be checked yet.
- **Implemented policies.** Scene `whole_recording` and Episode `event_markers`, the
  reference configurations. `fixed_duration` has no derivation; a contract using it
  fails with an `ExpectationError`.
- **Not recomputed.** Observation / occurrence id spelling, pose and calibration ids,
  and payload artifact ids (only their uniqueness, ownership and Scene / Episode
  sharing are checked). The full normalized `build_config` is checked for containing
  the reference configuration, not reproduced.
- **Source values.** CAN pose and battery values are compared between recording and
  manifest, not with the CAN extract (the README states the source, not the field
  mapping). Control values are compared with the extract.
- **Not covered here.** Validation / profile readiness (the baseline verifiers),
  derived resources (labels imported into LabelSets, sample views, alignment, exports),
  and the Recovery workflows.
- A saved observation (`verify --observation-in`) carries no captured recordings, so
  its Streaming Acquisition runs fail with `streaming_content`.
- **Captured recordings.** A streamed RobotRun pins its capture, whose `log_time` and
  `publish_time` are Capture's receive times; the rules compare the content derived
  from it (header stamps, payload bytes, decoded values), not those two fields. A
  selection is the whole contract or one fixture; other partial selections are refused.
