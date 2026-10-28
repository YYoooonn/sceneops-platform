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
[Ingress boundaries](../architecture/ingress-boundaries.md) §4, §4.1 and §6,
[ADR-007](../adr/007-canonical-ingestion-architecture.md) §8.3, §15.2 and §30.4–§30.6 and
[ADR-015](../adr/015-multi-source-platform.md) §6.2.

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
| clock domains | one declaration per clock the unit uses (the window's clock and every channel, pose or stream clock), sorted by clock, each `{origin: source, resolution_ns: 1}`; the manifest declares exactly those clocks, once each |
| lineage | `source_kind = recording`, `robot_run_id`, `recording_artifact_id = art-robotrun-<run>`, `recording_checksum`, window, `unit_key` |
| producer | fingerprint = `sha256(canonical_json({fingerprint_schema, producer_id, semantics_version, build_config, source: {source_kind, robot_run_id, recording_checksum}}))`; `build_config` carries every member of the reference build configuration; Scene producer `sceneops.recording_scene_builder` v4 |

### Platform state (observation → checks)

| Subject | Check |
| --- | --- |
| RobotRun | registered under the contract robot; recording artifact `art-robotrun-<run>` of kind `robot_run_recording`; the stored object's bytes equal the record; Recording Import pins the locked sha256; Streaming Acquisition pins its capture, whose per-topic counts equal the lock's and whose derived canonical content equals the locked recording's |
| units | the run's Scenes / Episodes are exactly the derived unit ids; each row's id recomputes from its binding; exactly `revisions_per_unit` revisions; one checksum per producer fingerprint |
| revision | `manifest_artifact_id = f(unit_id, manifest_checksum)`; window and projection columns equal the derivation |
| manifest | record kind, checksum and size equal the stored bytes; the bytes are canonical JSON; no DatasetVersion, unit id, status, location or execution field; schema version (`scene_manifest/v4`, `episode_manifest/v3`); a recording Scene carries no `native_ref`; lineage, producer and clock domains as above; content equal to the derivation |
| payloads | every reference has `kind = artifact` and resolves to an `observation_payload` record with exactly its checksum, size and media type, owned by the RobotRun; the object exists with that size (`--deep`: those bytes); one artifact per recorded message and extraction, shared by the Scene and the Episode |
| DatasetVersion | members are exactly the mode's derived unit ids, one per unit; each pin names a registered revision and its artifact id recomputes; `composition_checksum` recomputed from the pins; summaries (`scene_count`, `observation_count`, `keyframe_count`, `observed_channels`, `episode_count`) equal the derivation |

### LeRobot Episodes (raw pusht → expectations)

`tools/reference/lerobot_verify.py` with `verification/lerobot.py` holds the Episodes a
`SNAPSHOT_EPISODE_BUILDING` run produced from the pinned pusht snapshot to the raw tree
`data/raw/lerobot/pusht` and `config/reference/lerobot-pusht/episode_build_config.json`.
It reads the tree with its own pyarrow / json reader and implements the rules of
[Episode domain](../architecture/episode-domain.md) §3.2 and §4 and
[ADR-015](../adr/015-multi-source-platform.md) §5, §6.2, §6.5, §6.6 itself:

| Subject | Rule |
| --- | --- |
| content | inventory = the selected files, `{path, size_bytes, sha256}`, sorted; `content_checksum = sha256(canonical_json(files))`; `content_id = content-<sha256(canonical_json({content_id_schema, content_checksum}))[:32]>`; the registered content and the snapshot declaration must match |
| unit | one per native episode; `unit_key = str(episode_index)`; unit id with `source_kind = content`, `source_id = content_id` |
| time | `lerobot.frame_time`, origin `unit`, resolution 1 ns; frame `k` at `round_half_even(k · 10⁹ / fps)` on exact rationals; window `[0, t(length − 1) + 1)`; `frame_timeline {clock, fps, length}`; an episode whose recorded timestamps are not `float32(frame_index / fps)` must not be built |
| streams | topic = feature, `schema_name` = dtype, field `value` with the vector's source names as `component_names`; one occurrence per frame, id `<feature>-<k:08d>`, `native_ref = str(index)`; values equal to the Parquet values and exactly representable as float32 |
| video | `SourceFileRef` to the raw video: sha256 / size from the raw file, `video/mp4`, frame `round_half_even((from_timestamp + k / fps) · 10⁹)` with `from_timestamp` exact; the stored object has the raw size |
| task / transitions | `task_index` events at every task change with `{task, task_index}`; `next.*` transitions one per frame, verbatim |
| producer | `sceneops.lerobot_episode_builder` v1; fingerprint over the normalized configuration and `{source_kind: content, content_id, content_checksum}` |
| platform | one revision per unit; record, revision projection (transitions included), pin, canonical bytes and checksum; no snapshot id, upstream, upstream revision or licence token in the bytes; a DatasetVersion's members and composition checksum from the pins |

Negative controls (consistent faults on Episode 0, all must be detected):
`timestamp_drift` (a state 1 ns off its frame), `clock_origin` (`unix_epoch`),
`absolute_anchor` (window and timestamps given an epoch), `vector_order` (one state's
components reversed), `transition_value` (one `next.done` flipped),
`dropped_occurrence`, `content_binding` (another content, fingerprint recomputed),
`source_file_checksum`, `frame_selector` (1 ns), `missing_native_file` (the video
object absent from the store), `snapshot_in_manifest`.

### nuScenes Scenes (raw scene-0061 slice → expectations)

`tools/reference/nuscenes_scene_verify.py` with `verification/nuscenes_scene.py` holds
the Scenes a `SNAPSHOT_SCENE_BUILDING` run produced from the `nuscenes-scene-0061`
snapshot to the raw tree `data/raw/nuscenes`, selected by that snapshot's declaration,
and `config/reference/nuscenes-scene-0061/scene_build_config.json`. It reads the tables
with its own standard-library json reader and implements the rules of
[Scene domain](../architecture/scene-domain.md) §1 and §7 and
[ADR-015](../adr/015-multi-source-platform.md) §5, §6.2, §6.5, §6.6 itself:

| Subject | Rule |
| --- | --- |
| content | as for LeRobot: the selected files hashed independently, `content_checksum`, `content_id`; the registered content and the snapshot declaration must match |
| unit | one per native scene; `unit_key = scene.token`; a scene with a selected reading whose file the slice does not preserve must not be built (nine of the ten v1.0-mini scenes); `scene.name` must not appear in the bytes |
| time | `nuscenes.timestamp`, origin `unix_epoch`, resolution 1 000 ns; `timestamp_us · 1 000` for readings, ego poses and samples; window `[min, max + 1)` over every reading of the scene's samples, every channel |
| channels / frames | `sensor.channel`, `sensor.modality`, frame = channel; `global` (world), `ego` (ego) and one sensor frame per channel |
| calibrations / poses | `cal-<calibrated_sensor.token>` ego → channel with translation and rotation verbatim, the camera intrinsic for cameras only; `pose-<ego_pose.token>` global → ego |
| observations | every reading of the selected channels: id `<channel>-<rank:08d>` in (timestamp, token) order, `native_ref = sample_data.token`; `SourceFileRef` to the raw file with its sha256 and size and the D3 media type (`image/jpeg`, `application/x.nuscenes.lidar-pcd-bin`, `application/x.nuscenes.radar-pcd`); `calibration_id`, `ego_pose_id` from the reading's own tokens; image size for cameras |
| keyframes | one group per sample, `keyframe-<rank:06d>` in (timestamp, token) order, at `sample.timestamp`, the key-frame readings, `native_ref = sample.token` |
| producer | `sceneops.nuscenes_scene_builder` v1; fingerprint over `{channels sorted}` and `{source_kind: content, content_id, content_checksum}` |
| platform | one revision per unit; record (`robot_run_id` null), revision projection, pin, canonical bytes and checksum; no snapshot id, upstream, upstream revision, licence or scene-name token in the bytes; every referenced file stored with its raw size; a DatasetVersion's members, `scene_count` and composition checksum from the pins |

Negative controls (consistent faults on the first Scene, all must be detected):
`timestamp_drift` (a camera reading 1 µs off), `clock_origin` (`source`),
`window_shift` (1 µs early), `dropped_reading`, `sensor_file_swap` (two sensors' files
of one sample exchanged), `source_file_checksum`, `media_type` (lidar declared radar),
`ego_pose_link` (a reading linked to another reading's pose), `calibration_value`
(1 cm), `ego_pose_value` (0.5 m), `native_token` (a `native_ref` replaced),
`sample_token_swap` (two groups' sample tokens exchanged), `keyframe_member` (a key
frame dropped from its group), `content_binding` (another content, fingerprint
recomputed), `missing_native_file`, `snapshot_in_manifest`.

### Mixed-source DatasetVersions (every part → one composition)

`tools/reference/mixed_source_verify.py` with `verification/mixed_source.py` holds one
DatasetVersion composed of several sources and both domains to the composition that
`config/reference/mixed-sources/composition.json` declares: the Recording Import
RobotRun of scene-0061 (its Scene and its Episode), the scene-0061 slice's nuScenes
Scene and the 206 pusht Episodes. It derives every part with the rules above, from the
locked recording and the raw trees, and composes them itself:

| Subject | Rule |
| --- | --- |
| expected members | recording part: the unit ids `f(domain, recording, run_id, unit_key)` of the fixture's derived Scene and Episodes, `run_id` from the contract's `run_id_template`; content part: the derived units of the snapshot's raw tree, with the whole pin `(domain, unit_id, sha256(canonical_json(derived manifest)))` |
| expectations | `derive` writes them to `config/reference/mixed-sources/expectations.json` (`--write` only) and every run derives them again and fails on any difference (`expectations_drift`) |
| members | exactly the expected units, one member each, in their own domain; each pin's artifact id recomputes and names a registered revision of its unit (several revisions of a unit may exist; the member pins one) |
| units | the unit record's binding is the part's source and unit key; a content unit names no RobotRun and a recording unit no content; the id recomputes from the binding |
| manifests | stored bytes hash to the pin and agree with the record; canonical JSON; no membership, location or snapshot field, and no JSON token of the DatasetVersion's own dataset id or version name; a content member's bytes are exactly the derived manifest; every member also passes its part's manifest checks (recording: lineage, Scene / Episode content; content: the LeRobot or nuScenes manifest checks and the revision projection) |
| sources | each content part's content is registered with the raw content checksum, file count and size and is declared by its snapshot declaration (`format`, `upstream`, `upstream_revision`); each recording part's RobotRun is registered |
| composition | `composition_checksum` equals both `f(member pins)` and `f(expected pins)` (the content pins derived, the recording pins as verified); summaries (`scene_count`, `observation_count`, `keyframe_count`, `observed_channels`, `episode_count`) equal the derivation over every part; a recorded selector has exactly one clause per (part, domain), naming the part's source under the members' producer fingerprint |

It reads no payload object or preserved file: each part's own verifier does
(`reference_verify.py`, `nuscenes_scene_verify.py`, `lerobot_verify.py`).

Negative controls (consistent faults, all must be detected, on a composition by pins
and, with `selector_clause`, by selector): `content_timestamp_shift` (a pusht state 1 ns
off), `recording_timestamp_shift` (a recording Scene observation 1 ns off),
`membership_in_manifest` (the version's name written into a manifest), `wrong_pin` (a
checksum the unit never registered), `dropped_member`, `foreign_member` (a unit of
another content), `duplicate_unit` (two revisions of one unit), `wrong_domain` (an
Episode composed as a Scene), `synthetic_robot_run` (a content Scene bound to a
RobotRun), `source_swap` (a LeRobot unit under the nuScenes content),
`undeclared_content` (no snapshot declares the content), `summary_count`,
`summary_channels`, `checksum_of_other_pins`, `selector_clause` (a clause naming
another content).

## Negative controls

Every command runs its controls and fails when one is not detected. A *consistent*
fault rewrites everything the platform derives from the corrupted value (canonical
re-serialization, manifest checksum and artifact id, revision row, member pins,
composition checksum), so only the independent expectations can catch it.

| Layer | Controls (code that must appear) |
| --- | --- |
| sources | dropped `/tf` message (`source_message_count`), 1 ns camera stamp shift (`source_timestamp`), changed point-cloud byte (`source_payload_bytes`), re-locked recording (`lock_recording_checksum`), 1 ns label anchor shift (`source_label_anchor`), dropped label (`source_label_content`) |
| expectations | committed Scene window off by 1 ns, expectations from another build configuration (`expectations_drift`) |
| platform | dropped observation, 1 ns shift, payload bytes rebound consistently (`scene_observations`); changed stored payload byte (`payload_object_checksum`); member pins an unregistered revision (`member_pin`); member artifact id of another revision (`pin_artifact_id`); Episode re-based to unit-relative time on an absolute clock (`window`, `episode_occurrences`); absolute anchor added to poses (`scene_poses`, `window`); a Scene clock declared `unix_epoch`, an Episode clock declared `unit`, the window clock's declaration dropped (`clock_domains`); binding to another recording or RobotRun with a recomputed fingerprint (`source_binding`); another unit key (`unit_identity`); missing or foreign member (`composition_members`); wrong `composition_checksum`; wrong summary (`composition_summary`); changed action value, dropped end marker (`episode_occurrences`); build config changed without fingerprint (`producer_fingerprint`); pretty-printed manifest (`manifest_canonical`); `dataset_version` in a manifest (`manifest_forbidden_field`); missing payload or manifest object; substituted recording (`robot_run_recording`); second revision (`revision_count`); one message through two payload artifacts (`payload_identity`) |

`verify` runs the platform controls on a copy of the collected observation, only when
that observation passes.

## Commands

```text
make reference-verify-sources [FIXTURE=<id>]   raw nuScenes <-> recordings <-> lock <-> labels   no stack
make reference-expectations [WRITE=1]          derive and compare (WRITE=1: replace the file)   no stack
make reference-verify-selftest                 synthetic state passes, every control detected   no stack
make reference-verify [DEEP=1] [MODE=<mode>] [FIXTURE=<id>]   the platform's contract state, read-only
make reference-contract-verify                 the contract state check; runs reference-verify
make reference-verify-lerobot-selftest [EPISODE=<n>]          LeRobot rules on a synthetic state   no stack
make reference-verify-lerobot SNAPSHOT_ID=<id> [EPISODE=<n>] [DATASET=<id>/<version>]
                                               the platform's Episodes of the pusht snapshot, read-only
make reference-verify-nuscenes-scene-selftest  nuScenes Scene rules on a synthetic state   no stack
make reference-verify-nuscenes-scene SNAPSHOT_ID=<id> [UNIT_KEY=<token>] [DATASET=<id>/<version>]
                                               the platform's Scenes of the scene-0061 slice, read-only
make reference-verify-mixed-expectations [WRITE=1]   mixed composition: derive and compare   no stack
make reference-verify-mixed-selftest           mixed composition rules on a synthetic state   no stack
make reference-verify-mixed DATASET=<id>/<version>    the platform's mixed DatasetVersion, read-only
```

`make test-infrastructure SUITE=lerobot-episodes` builds pusht on a disposable runtime
and runs `reference-verify-lerobot` for Episode 0 and then for the whole corpus and its
DatasetVersion. `make test-infrastructure SUITE=nuscenes-scenes` builds the scene-0061
slice on a disposable runtime and runs `reference-verify-nuscenes-scene` with its
DatasetVersion. `make test-infrastructure SUITE=mixed-sources` builds all three parts on
a disposable runtime, composes them by pins and by selector, runs
`reference-verify-mixed` on both versions and each part's own verifier.

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

- **Content sources.** LeRobot Episodes of the pusht snapshot (`lerobot_verify.py`) and
  nuScenes Scenes of the scene-0061 slice (`nuscenes_scene_verify.py`) have rules. MS-14
  execution provenance (the Job result naming the snapshot, the manifest record
  keeping the first build's Job) and the cross-source comparison with the RobotRun
  Scene of scene-0061 are checked by the `lerobot-episodes` / `nuscenes-scenes`
  infrastructure suites, not by the verifiers.
- **Mixed compositions.** One composition is specified
  (`config/reference/mixed-sources/composition.json`). The recording members' manifest
  checksums are not derived (payload artifact ids are not recomputed), so their pins
  enter the expected composition checksum as verified by the recording manifest checks.
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
