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

## Where baselines are used

| Consumer | Baseline |
| --- | --- |
| `make e2e-batch-canonical` | its own unique baseline of one fixture; also proves bootstrap re-runs change nothing |
| `make e2e-scene-ml`, `make e2e-episode-learning` | a unique baseline of one fixture per run, or `BASELINE_ID=<id>` of a baseline that contains exactly that fixture |
| `make e2e-cleanroom` | the persistent `ref-smoke-1`, built from fresh platform state |
| `make test-infrastructure` | the persistent `ref-smoke-1` (create-or-verify); tests build one RobotRun's Scenes / Episodes into throwaway DatasetVersions |

The baseline is independent of `make local-reset`: reset only destroys generated
state, the bootstrap (re)creates the baseline on top of a running stack, and the
prepared recordings under `data/reference` survive it.
