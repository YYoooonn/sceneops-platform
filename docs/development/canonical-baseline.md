# Canonical baseline

> Developer / test orchestration, not a Pipeline. Decision record:
> [ADR-007](../adr/007-canonical-ingestion-architecture.md) §34. Input:
> the [reference corpus](./reference-corpus.md).
>
> `canonical-bootstrap` and `streaming-bootstrap` (with their read-only `*-verify` and
> `streaming-compare`) are the building blocks of `make reference-contract-bootstrap` /
> `reference-contract-verify`, which are the commands to use. They stay callable for one
> fixture or one mode, and they are not part of the validation surface
> ([test-matrix.md](./test-matrix.md)).

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

The platform has two ingestion modes, and a baseline exists for each:

```text
Recording Import        existing MCAP -> RobotRun           ref-<selection>         canonical-bootstrap
Streaming Acquisition   ROS 2 -> Kafka -> capture -> RobotRun   stream-ref-<selection>  streaming-bootstrap
```

Preparing the source (nuScenes -> locked MCAP) is neither: it is
[source preparation](./reference-corpus.md). The promoted full-corpus baselines of
both modes together are the [golden reference contract](./reference-contract.md).

It contains **nothing derived**: no labels, sample views, ScenarioSets,
predictions, evaluations, aligned episodes or learning exports. Those are L3
workflows that run on top of a baseline (`make e2e-scene-ml`,
`make e2e-episode-learning`) and never enter it.

## Commands

```text
make canonical-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10 | FIXTURE=<id>]
make canonical-verify    [same selection]                      read-only re-check
```

- The bootstrap **converts nothing**. If a fixture's recording is not prepared, or
  does not match `corpus.lock.json`, it stops and names the problem; the fix is
  `make reference-data-bootstrap REFERENCE_SCOPE=<scope>`.
- `tools/baselines/canonical/canonical_bootstrap.sh` builds the baseline;
  `tools/baselines/canonical/canonical_verify.sh` is the read-only check the bootstrap ends
  with (`tools/baselines/canonical/baseline_lib.sh`). Both use only FastAPI and one-shot
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
`FIXTURE=<id>` selects one fixture instead. A selection only chooses **which
fixtures are acted on**: the baseline is always the
[golden reference contract's](./reference-contract.md) `recording_import` baseline
(`BASELINE_ID=ref-nuscenes-mini-full-10`), so

| Selection | Fixtures acted on | Baseline |
| --- | --- | --- |
| `REFERENCE_SCOPE=smoke-1` | `scene-0061` | `ref-nuscenes-mini-full-10` |
| `REFERENCE_SCOPE=nuscenes-mini-full-10` | all ten | `ref-nuscenes-mini-full-10` |
| `FIXTURE=scene-0103` | `scene-0103` | `ref-nuscenes-mini-full-10` |

`smoke-1` is `scene-0061` of the full baseline, never a second baseline with
RobotRuns and a DatasetVersion of its own. `canonical-verify` on a selection that is
a part of the baseline requires the selected RobotRuns (and everything of the
DatasetVersion to belong to a RobotRun of the baseline's robot); on the whole scope
it requires exactly the contract's.

`BASELINE_ID` names everything the baseline owns:

| Entity | Identity |
| --- | --- |
| RobotRun (one per fixture) | `run-<BASELINE_ID>-<fixture>` |
| Robot | `robot-<BASELINE_ID>` |
| DatasetVersion | `sceneops-<BASELINE_ID>` / `baseline` |

A baseline of any other `BASELINE_ID` registers RobotRuns that are not part of the
reference contract. Only a disposable runtime may hold them
(`DISPOSABLE_RUNTIME=1`; see [test-matrix.md](./test-matrix.md#test-state-classes));
the bootstrap refuses otherwise. `DATASET_ID` may name another DatasetVersion: the
bootstrap then builds the Scenes and Episodes of the (reference) RobotRuns into it
and registers no RobotRun, which is how the L3 journeys keep the reference
DatasetVersion untouched.

## create-or-verify

- A RobotRun that is already registered is **reused**, never re-published, and must
  pin exactly the fixture's locked recording (sha256 and size): a mismatch fails
  loudly. Recovery from a mismatched baseline is an explicit `make local-reset`
  and rebuild.
- A RobotRun whose Scenes and Episodes are all in the DatasetVersion, validated,
  profiled and ready is **reused as it is**: no build pipeline runs over it. The cached
  recordings are hashed against the lock only when a RobotRun has to be published, so
  the bootstrap of a complete baseline reads the platform and writes nothing.
- A Scene / Episode build over an incomplete or changed scope **converges** on the
  registered revisions (`replace: false`; the registrar reports every unit as
  unchanged).
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

## Streaming baseline (Streaming Acquisition)

The same fixtures reach canonical state a second way: replayed from the locked
reference MCAP through the streaming transport. The streaming baseline is the
persistent counterpart of the Recording Import baseline, built from the same corpus with the
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
make streaming-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10 | FIXTURE=<id>] [RATE=...]
make streaming-verify    [same selection]            read-only re-check
make streaming-compare   [same selection]            read-only: Recording Import baseline vs Streaming Acquisition baseline
```

- `BASELINE_ID` is the contract's `streaming_acquisition` baseline,
  `stream-ref-nuscenes-mini-full-10`, whatever the selection (`smoke-1` is
  `scene-0061` of it). Robot, RobotRun (`run-<BASELINE_ID>-<fixture>`) and DatasetVersion
  follow the same identity rules as the Recording Import baseline, so the two never share
  a RobotRun set or a DatasetVersion.
- `RATE` overrides every fixture's replay rate. The bootstrap needs Kafka (it starts
  the broker only when a RobotRun has still to be streamed), `make local-up`, and the
  fixtures prepared by `make reference-data-bootstrap`. It reads no raw dataset and
  converts nothing.
- Publication and registration are the production commands of
  [ADR-008](../adr/008-acquisition-lifecycle-reliability.md); nothing writes PostgreSQL
  or MinIO directly.
- `tools/baselines/streaming/streaming_lib.sh` holds the acquisition path of the streaming
  baseline.

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

### Recording Import and Streaming Acquisition compared

`make streaming-compare` verifies both baselines read-only, then checks at corpus
level: the same fixture set and locked recordings; equal RobotRun, Scene, Episode and
message totals; and per fixture the locked, imported and streamed message counts and
per-channel counts agree, the imported RobotRun pins the locked recording and the
streamed one its own capture. It loads every Scene and Episode manifest of both
sides and requires an equal semantic projection per unit key
(`semantic_scene_content` / `semantic_episode_content`, ADR-007 I-35). It loads no
recording payload: payload-level equivalence of the two acquisitions is proven on one
fixture by `make e2e-streaming-equivalence`, which reads the two registered RobotRuns.

### Limitations

- Fixtures are streamed one at a time, sequentially; the run owns its Kafka offset
  range, which the capture receipt check relies on.
- Kafka keeps every streamed run until the broker's retention expires its segments (the
  repository configures none; the local broker's telemetry topic reports 7 days), so its
  disk use grows by about one recording per fixture meanwhile. Reset Kafka only
  deliberately; see [Disk hygiene](./local-development.md#disk-hygiene).
- The streamed recording's checksum is specific to the execution that captured it
  and is not reproducible; the baseline is verified by its registered facts, not by
  a fixed checksum.
- An interrupted capture resumes only if the run's RUN_END is already in Kafka; if the
  replay itself was interrupted, the run's partial state is cleared by hand.

## Where baselines are used

| Consumer | Baseline | Test-state class |
| --- | --- | --- |
| `make reference-contract-bootstrap` | both contract baselines, all ten fixtures | `REFERENCE_CONTRACT` |
| `make canonical-bootstrap`, `streaming-bootstrap`, `*-verify`, `streaming-compare` | the contract's baselines, restricted to the selection (`smoke-1` = `scene-0061`) | `REFERENCE_CONTRACT` (the bootstraps) / `REFERENCE_READ_ONLY` (the verifiers) |
| `make e2e-scene-ml`, `make e2e-episode-learning` | the contract's Recording Import RobotRun of `SOURCE_UNIT` (default `scene-0061`); Scenes, Episodes and everything derived are written to the fixed Datasets `sceneops-test-scene-ml` / `sceneops-test-episode-learning` (DatasetVersion `baseline`), which a repeated run reuses | `REFERENCE_DERIVED` |
| `make e2e-streaming-equivalence` | the contract's Recording Import and Streaming Acquisition RobotRuns of `SOURCE_UNIT` (default `scene-0061`), their recordings and their Scenes and Episodes; it creates nothing | `REFERENCE_READ_ONLY` |
| `make e2e-cleanroom` | resets the runtime, reconstructs the whole contract from the preserved reference inputs, then runs both L3 journeys on `scene-0061` into their fixed Datasets | `CLEANROOM_ACCEPTANCE` |
| `make test-infrastructure` (`SUITE=pipelines`) | the contract's Recording Import RobotRun of `smoke-1`, create-or-verify seeded into the command's own disposable database and bucket (the reference environment is not read); the tests build its Scenes / Episodes into the fixed `sceneops-test-infra-pipelines` Dataset, one named DatasetVersion per test | `DISPOSABLE_ENVIRONMENT` |

The baseline is independent of `make local-reset`: reset only destroys generated
state, the bootstrap (re)creates the baseline on top of a running stack, and the
prepared recordings under `data/reference` survive it.
