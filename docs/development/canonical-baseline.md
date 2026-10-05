# Canonical baseline

> Developer / test orchestration, not a Pipeline. Decision record:
> [ADR-007](../adr/007-canonical-ingestion-architecture.md) §34.

A **canonical baseline** is a reproducible L1/L2 state on a running stack:

```text
dataset fixture (nuScenes mini, read-only)
  -> dataset-acquisition container      finalized sensor-bearing MCAP
  -> recording-publisher container      L1 conformance + publication
  -> POST /robot-runs:register          RobotRun                       (L1)
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
make canonical-bootstrap [BASELINE_ID=canonical] [SCENE=scene-0061]   create-or-verify the baseline
make canonical-verify                                                  read-only re-check
```

- `scripts/canonical/canonical_bootstrap.sh` builds the baseline;
  `scripts/canonical/canonical_verify.sh` is the read-only check the bootstrap
  ends with (`scripts/canonical/baseline_lib.sh`). Both use only FastAPI and
  one-shot containers. The bootstrap prints one JSON summary on stdout
  (identity, RobotRuns, Scene and Episode ids); progress goes to stderr.
- The Scene and Episode build configurations are the files under
  `config/baselines/` (`scene_build_config.json`, `episode_build_config.json`):
  explicit source-semantic channel, stream, time and segmentation declarations.
  Every journey that builds from the baseline recording reads the same files, so
  "the same configuration" has one definition.

## Identity

`BASELINE_ID` (default `canonical`) names everything the baseline owns:

| Entity | Identity |
| --- | --- |
| RobotRun (one per source scene) | `run-<BASELINE_ID>-<scene>` |
| Robot | `robot-<BASELINE_ID>` |
| DatasetVersion | `sceneops-<BASELINE_ID>` / `baseline` |

`SCENE` (or `SOURCE_UNITS`, space-separated) selects the nuScenes scene(s);
`scene-0061` is the default. The persistent baseline is `canonical`. A journey
that mutates its scope uses a unique id per run
(`e2e-batch-<timestamp>-<pid>`).

## create-or-verify

- A RobotRun that is already registered is **reused**: its recording is not
  acquired or published again.
- A Scene / Episode build over an unchanged scope **converges** on the
  registered revisions (`replace: false`; the registrar reports every unit as
  unchanged).
- A changed producer or build configuration fails **loudly at registration**; it
  never replaces canonical membership. Recovery from a mismatched baseline is an
  explicit `make local-reset` and rebuild.
- Nothing writes PostgreSQL or MinIO directly.

## What `canonical-verify` checks

Through the API and artifact pins only:

- every baseline RobotRun is registered and its recording ArtifactRecord pins a
  `sha256` checksum;
- every Scene and Episode of the DatasetVersion points at a baseline RobotRun and
  pins exactly the checksum its manifest ArtifactRecord carries;
- every Scene and Episode is validated, profiled and `ready` at its **current**
  revision;
- the DatasetVersion's registrar-owned Scene and Episode summaries equal the
  registered membership.

## Where baselines are used

| Consumer | Baseline |
| --- | --- |
| `make e2e-batch-canonical` | its own unique baseline; also proves bootstrap re-runs change nothing |
| `make e2e-scene-ml`, `make e2e-episode-learning` | a unique baseline per run, or `BASELINE_ID=<id>` |
| `make e2e-cleanroom` | the persistent `canonical` baseline, built from fresh platform state |
| `make test-infrastructure` | the persistent `canonical` baseline (create-or-verify); tests write only into throwaway DatasetVersions |

The baseline is independent of `make local-reset`: reset only destroys generated
state, the bootstrap (re)creates the baseline on top of a running stack.
