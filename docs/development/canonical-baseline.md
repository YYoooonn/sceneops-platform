# Canonical baseline

> Developer / test orchestration, not a Pipeline. Decision record:
> [ADR-007](../adr/007-canonical-ingestion-architecture.md) §34. Input:
> the [reference corpus](./reference-corpus.md).

A **canonical baseline** is a reproducible L1/L2 state on a running stack, built
from prepared reference-corpus recordings:

```text
reference corpus (corpus.json + corpus.lock.json), prepared recordings
  -> reference resolve                  fixtures and their verified, locked recordings
  -> recording-publisher container      publication, read in place from the read-only mount
  -> POST /robot-runs:register          one RobotRun per fixture                (L1)
  -> recording_scene_building           canonical Scenes, validated, profiled   (L2)
  -> recording_episode_building         canonical Episodes, validated, profiled (L2)
  -> verify                             read back through the API
```

It contains **nothing derived**: no labels, sample views, ScenarioSets,
predictions, evaluations, aligned episodes or learning exports. Those are L3
workflows that run on top of a baseline (`make e2e-scene-ml`,
`make e2e-episode-learning`) and never enter it.

## Commands

```text
make canonical-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10 | FIXTURE=<id>] [BASELINE_ID=...]
make canonical-verify    [same selection]                      read-only re-check
```

- The bootstrap **converts nothing**. If a fixture's recording is not prepared, or
  does not match `corpus.lock.json`, it stops and names the problem; the fix is
  `make reference-data-bootstrap REFERENCE_SCOPE=<scope>`.
- `scripts/canonical/canonical_bootstrap.sh` builds the baseline;
  `scripts/canonical/canonical_verify.sh` is the read-only check the bootstrap ends
  with (`scripts/canonical/baseline_lib.sh`). Both use only FastAPI and one-shot
  containers. The bootstrap prints one JSON summary on stdout (below); progress
  goes to stderr.
- The Scene and Episode build configurations are the files under
  `config/baselines/` (`scene_build_config.json`, `episode_build_config.json`):
  explicit source-semantic channel, stream, time and segmentation declarations.
  Every journey that builds from a baseline recording reads the same files, so
  "the same configuration" has one definition. Scene and Episode builds run
  independently for every RobotRun. The reference baseline is a stable real-data
  input, not a segmentation benchmark: its Scene configuration is
  `whole_recording` (one Scene per RobotRun) and its Episode configuration cuts
  one Episode per recording from the mission markers. Fixed-duration Scene
  segmentation remains a supported policy, exercised by the builder tests and the
  infrastructure replacement test, never by the baseline.
- `config/baselines/baseline_shape.json` states the shape the reference baseline
  is expected to have (`scenes_per_robot_run`, `episodes_per_robot_run`, both 1).
  `canonical-verify` asserts it per fixture; membership itself is always read from
  the API. The corpus lock never holds canonical counts.

## Selection and identity

`REFERENCE_SCOPE` (default `smoke-1`) selects the fixtures of a corpus scope;
`FIXTURE=<id>` selects one fixture instead. `BASELINE_ID` names everything the
baseline owns and defaults to the selection, so a smoke baseline and a full
baseline never share a RobotRun set or a DatasetVersion:

| Selection | Default `BASELINE_ID` |
| --- | --- |
| `REFERENCE_SCOPE=smoke-1` | `ref-smoke-1` |
| `REFERENCE_SCOPE=nuscenes-mini-full-10` | `ref-nuscenes-mini-full-10` |
| `FIXTURE=scene-0103` | `ref-scene-0103` |

| Entity | Identity |
| --- | --- |
| RobotRun (one per fixture) | `run-<BASELINE_ID>-<fixture>` |
| Robot | `robot-<BASELINE_ID>` |
| DatasetVersion | `sceneops-<BASELINE_ID>` / `baseline` |

A journey that mutates its scope uses a unique id per run
(`e2e-batch-<timestamp>-<pid>`).

## create-or-verify

- A RobotRun that is already registered is **reused**, never re-published, and must
  pin exactly the fixture's locked recording (sha256 and size): a mismatch fails
  loudly. Recovery from a mismatched baseline is an explicit `make local-reset`
  and rebuild, or another `BASELINE_ID`.
- A Scene / Episode build over an unchanged scope **converges** on the registered
  revisions (`replace: false`; the registrar reports every unit as unchanged).
- A changed producer or build configuration fails **loudly at registration**; it
  never replaces canonical membership.
- Nothing writes PostgreSQL or MinIO directly. Prepared recordings are published
  from the read-only reference mount and never copied into another volume.

## Summary

The bootstrap and `canonical-verify` print the same JSON, deterministic for an
unchanged baseline: `baseline_id`, `robot_id`, `dataset_id`, `dataset_version`,
`reference` (corpus, scope or fixture), `robot_run_ids`, the Scene and Episode ids
and counts, `totals` (fixtures, RobotRuns, Scenes, Episodes, recording bytes,
messages) and, per fixture, `fixture_id`, `source_unit`, `robot_run_id`,
`recording_sha256`, `recording_bytes`, `message_count` (locked), Scene and Episode
ids and counts and how many are validated, profiled and ready. Canonical Scene and
Episode counts are an output of the baseline, checked against
`baseline_shape.json`; the corpus lock never holds them.

## What `canonical-verify` checks

Through the API and artifact pins only; it reads the lock but no recording:

- the RobotRuns of the baseline's robot are exactly the selected fixtures' runs:
  none missing, none unexpected;
- each RobotRun's recording ArtifactRecord pins the fixture's locked `sha256` and
  size;
- every Scene and Episode of the DatasetVersion belongs to one of those runs, every
  fixture has the expected number of each (`baseline_shape.json`), and each pins exactly the checksum its
  manifest ArtifactRecord carries;
- every Scene and Episode is validated, profiled and `ready` at its **current**
  revision;
- the DatasetVersion's registrar-owned Scene and Episode summaries equal the
  registered membership.

## Streaming baseline

The same fixtures reach canonical state a second way: replayed from the locked
reference MCAP through the streaming transport. The streaming baseline is the
persistent counterpart of the batch baseline, built from the same corpus with the
same Scene and Episode build configurations.

```text
locked reference MCAP (one per fixture)
  -> reference replay          dataset-replay container; the corpus and cache read-only, no raw dataset
  -> ROS 2 -> bridge -> Kafka -> capture      finalized on the run's RUN_END; MCAP + capture receipt
  -> publish-pending           publisher CLI, from the receipt
  -> reconcile --once --apply  submits REGISTER_ROBOT_RUN
  -> one streamed RobotRun per fixture
  -> recording_scene_building / recording_episode_building   (config/baselines/, whole_recording)
  -> verify                    read back through the API
```

```text
make streaming-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10 | FIXTURE=<id>] [BASELINE_ID=...] [RATE=...]
make streaming-verify    [same selection]            read-only re-check
make streaming-compare   [same selection]            read-only: batch baseline vs streaming baseline
```

- `BASELINE_ID` defaults to `stream-ref-<selection>` (`stream-ref-smoke-1`,
  `stream-ref-nuscenes-mini-full-10`, `stream-ref-<fixture>`). Robot, RobotRun
  (`run-<BASELINE_ID>-<fixture>`) and DatasetVersion follow the same identity rules as
  the batch baseline, so the two never share a RobotRun set or a DatasetVersion.
- `RATE` overrides every fixture's replay rate. The bootstrap needs Kafka (it starts
  the broker if absent), `make local-up`, and the fixtures prepared by
  `make reference-data-bootstrap`. It reads no raw dataset and converts nothing.
- Publication and registration are the production commands of
  [ADR-008](../adr/008-acquisition-lifecycle-reliability.md); nothing writes PostgreSQL
  or MinIO directly.
- `scripts/streaming/streaming_lib.sh` holds the acquisition path shared with
  `make e2e-streaming-equivalence`.

### create-or-verify, per fixture

The next step is chosen from the platform's durable state; a registered RobotRun is
never replayed over.

| State of the fixture's run | Action |
| --- | --- |
| RobotRun registered; one Scene and one Episode, validated, profiled, ready | reused |
| RobotRun registered; Scene or Episode missing or not ready | built (converges on registered revisions) |
| capture finalized or published, registration pending / active / stalled / transiently failed | `publish-pending` and `reconcile --apply`; not replayed |
| capture unfinished (interrupted) | capture resumed from Kafka's committed offsets; not replayed |
| nothing | replayed (streamed) |
| any other reconciler state (conflict, integrity incident, permanent failure) | fails and names the state; the bootstrap repairs nothing |

After a run's recording is registered and byte-identical to its capture, the
transient capture files of that run are removed from the recordings volume; the
durable state is in the ArtifactStore and PostgreSQL. A capture that is not provably
published is kept.

Before each fixture it would replay, the bootstrap requires `MIN_FREE_GIB` (default 6)
free on both the host volume and the Docker VM disk, and otherwise stops; it resumes
from the durable state when re-run. Per fixture it logs `streaming_fixture {json}`
(action and replay, capture, publish-and-register, Scene build and Episode build
seconds) and `streaming_storage {json}` (free space, MinIO, Kafka and recordings
sizes) on stderr. A pipeline still running after `STALL_REPORT_SECONDS` (default 240)
is reported with worker CPU, memory and log tails; a rejected pipeline execute is
reported with its HTTP status.

### What `streaming-verify` checks

The checks of `canonical-verify` (membership, Scene / Episode shape, manifest pins,
readiness, DatasetVersion summaries), with the recording check replaced. A streamed
RobotRun pins the **captured** recording, not the locked one: capture stamps its own
receive times, so the bytes differ. Instead, through the RobotRun's registered
manifest:

- the recording ArtifactRecord equals the manifest's recording, and is not the locked
  recording itself;
- the manifest is a Kafka capture of the baseline's robot, on the recording clock;
- the message count and the per-channel counts equal the lock's for that fixture.

The summary is the canonical baseline summary plus `transport` and, per fixture,
`streamed_recording` (sha256, size, message count, per-channel counts);
`recording_sha256` / `recording_bytes` remain the locked source recording's. It is
deterministic for an unchanged baseline.

### Batch and streaming compared

`make streaming-compare` verifies both baselines read-only, then checks at corpus
level: the same fixture set and locked recordings; equal RobotRun, Scene, Episode and
message totals; and per fixture the locked, batch and streamed message counts and
per-channel counts agree, the batch RobotRun pins the locked recording and the
streamed one its own capture. It loads every Scene and Episode manifest of both
sides and requires an equal semantic projection per unit key
(`semantic_scene_content` / `semantic_episode_content`, ADR-007 I-35). It loads no
recording payload: payload-level equivalence of the two acquisitions is proven on one
fixture by `make e2e-streaming-equivalence`.

### Limitations

- Fixtures are streamed one at a time, sequentially; the run owns its Kafka offset
  range, which the capture receipt check relies on.
- Kafka retains every streamed run (no retention is configured); the broker's disk use
  grows by about one recording per fixture. Reset Kafka only deliberately.
- The streamed recording's checksum is specific to the execution that captured it
  and is not reproducible; the baseline is verified by its registered facts, not by
  a fixed checksum.
- An interrupted capture resumes only if the run's RUN_END is already in Kafka; if the
  replay itself was interrupted, the run's partial state is cleared by hand.

## Where baselines are used

| Consumer | Baseline |
| --- | --- |
| `make e2e-batch-canonical` | its own unique baseline of one fixture; also proves bootstrap re-runs change nothing |
| `make e2e-scene-ml`, `make e2e-episode-learning` | a unique baseline of one fixture per run, or `BASELINE_ID=<id>` of a baseline that contains exactly that fixture |
| `make streaming-bootstrap`, `streaming-verify`, `streaming-compare` | the persistent `stream-ref-<selection>`; the batch baseline is only read |
| `make e2e-streaming-equivalence` | the persistent `ref-<selection>` (`ref-smoke-1` by default) as the batch arm: verified read-only when registered, created only when absent, never rebuilt; the streamed arm is a unique RobotRun in its own DatasetVersion |
| `make e2e-cleanroom` | the persistent `ref-smoke-1`, built from fresh platform state |
| `make test-infrastructure` | the persistent `ref-smoke-1` (create-or-verify); tests build one RobotRun's Scenes / Episodes into throwaway DatasetVersions |

The baseline is independent of `make local-reset`: reset only destroys generated
state, the bootstrap (re)creates the baseline on top of a running stack, and the
prepared recordings under `data/reference` survive it.
