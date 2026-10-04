# Canonical development baseline (v0.0)

> **Unavailable until ADR-007 implementation step 11.** v0.0 was built by
> the removed `dataset_scene_ingestion` pipeline, and its Scene contract
> (counts, ground truth) cannot be produced from recordings yet.
> `make canonical-bootstrap` / `make canonical-verify` exit with an explicit
> message until the baseline is regenerated from recordings. This page
> describes the v0.0 contract as frozen.

The authoritative doc for SceneOps' permanent development baseline dataset
family. `make help` is the quick reference; this explains the *why* and the
exact contract `make canonical-verify` checks. See
[local-development.md](local-development.md) for the general local stack and
[test-matrix.md](test-matrix.md) for how this differs from the E2E/test
surface.

## What v0.0 is

`v0.0` is a frozen, source-controlled baseline built from the 10 real
nuScenes `v1.0-mini` scenes already validated for both the canonical Scene
flow and the CAN-based robot-learning flow — never synthetic fixtures. It
materializes as **three independent DatasetVersions**:

| DatasetVersion             | Scenes | Episodes | Purpose                                   |
|-----------------------------|--------|----------|--------------------------------------------|
| `sceneops-scenes/v0.0`      | 10     | 0        | Scene-only domain case                     |
| `sceneops-episodes/v0.0`    | 0      | 10       | Episode-only domain case                   |
| `sceneops-canonical/v0.0`   | 10     | 10       | **Default development baseline**           |

Each is fully independent canonical identity — its own `DatasetRecord`,
`DatasetVersionRecord`, and Scene/Episode rows. None of the three shares a
database row with another, even though all three derive from the same 10
real source scenes (see "Cross-dataset isolation" below). `sceneops-scenes`
and `sceneops-episodes` exist specifically to prove Scene/Episode domain
independence on `DatasetVersionRecord` (see
`packages/sceneops-core/sceneops_core/datasets/schemas/summaries.py`); day
to day development should use `sceneops-canonical/v0.0`.

The single source of truth for scene ordering, dataset identities, robot
config, and expected counts is
[`config/baselines/canonical-v0.0.yaml`](../../config/baselines/canonical-v0.0.yaml)
— every script under `scripts/canonical/` reads it, none hardcode the scene
list independently.

## Immutability

`v0.0` is not `latest`, not rolling, and never auto-upgraded. Once
successfully materialized, its logical contents (which scenes, which
counts, which derived artifacts) must not change. A future baseline
revision creates `config/baselines/canonical-v0.1.yaml` and a new
`baseline_version`; it never edits `v0.0`'s spec or regenerates its content
in place. There is no mutable "current baseline version" abstraction
anywhere in the codebase — `sceneops-canonical/v0.0` is just a normal
DatasetVersion identity a developer chooses to point at.

## Commands

```bash
make local-up                 # bring up the local stack (idempotent)
make canonical-bootstrap      # create-or-verify the v0.0 baseline family
```

or, from a fully clean generated state:

```bash
FORCE=1 make local-reset      # destructive: wipes Postgres/Redis/MinIO,
                               # PRESERVES data/raw/nuscenes
make canonical-bootstrap
```

`make canonical-bootstrap` (`scripts/canonical/canonical_bootstrap.sh`) is
**create-or-verify, never mutate**:

- All three baselines absent → creates all three (real nuScenes ingestion,
  real CAN replay → ROS2 → fresh MCAP → Episode building → alignment →
  `export_learning_data` v2-sharded → curation), then verifies what it just
  built.
- All three baselines present and matching the v0.0 contract → verifies only
  (read-only), exits 0, **no mutation, no duplicate rows, no new pipeline
  runs**.
- Anything else (partial, or present-but-mismatched) → **fails loudly**.
  Never silently repairs a frozen baseline. Recover with
  `FORCE=1 make local-reset && make canonical-bootstrap`.

`make canonical-verify` (`scripts/canonical/canonical_verify.sh`) runs the
same deep contract checks but is unconditionally read-only — it never
creates or dispatches anything, and fails if the baseline family isn't
already fully materialized.

`reset != image rebuild`: like `local-reset`, `canonical-bootstrap` reuses
whichever `api`/`worker`/`ros2` images are already built. If you've changed
application source, run `make compose-build` first.

## Default development dataset

Unless you're specifically testing the Scene-only or Episode-only domain
case, point manual exploration (curl, `make api-shell`, notebooks) at:

```
DATASET_ID=sceneops-canonical DATASET_VERSION=v0.0
```

This is a documentation convention only — no core/service code hardcodes
this identity, and it does not change production API semantics.
`DATASET_ID`/`DATASET_VERSION` Make defaults for the `e2e-*`/`verify-*`
targets are unchanged and continue to default to the independent,
ephemeral `test-e2e-*` identities (see below) — E2E tests never run against
the canonical baseline.

## Independence from E2E state

`test-e2e-*` (via `scripts/e2e/lib.sh`'s `resolve_e2e_fixture`) and
`sceneops-{scenes,episodes,canonical}` are disjoint identity namespaces.
E2E tests (`make e2e-scene`, `make e2e-robot-learning`, `make e2e-cleanroom`,
...) never read or write the canonical baseline, and `canonical-bootstrap`
never touches `test-e2e-*` state. The canonical baseline is **development
data, not a test fixture** — it does not replace unit/integration fixtures,
`test-e2e-core`, `test-e2e-raw-log`, the LeRobot golden fixture, or the
Phase 5 benchmark fixture, and no test suite depends on it being present.

## Physical artifact reuse

Each of the 10 scenes' real CAN replay → MCAP recording happens **once**
per bootstrap run (fresh every time the CREATE path runs — `rm -rf` then a
real ROS2 replay, same as `e2e_robot_learning.sh`) and is reused by
reference (`robot_run_id`) to build Episodes into **both**
`sceneops-episodes/v0.0` and `sceneops-canonical/v0.0` — `build_episodes`
resolves an Episode purely from the RobotRun's registered recording plus the
dispatching pipeline's own `dataset_id`/`dataset_version`
(`apps/worker/sceneops_worker/jobs/dataset/build_episodes.py`), so reusing
one RobotRun across two independent, dataset-scoped Episode builds is
already-safe existing behavior — not new architecture. Scene ingestion
(Postgres `SceneRecord` + ArtifactStore `SceneManifest`) is **not**
deduplicated between `sceneops-scenes/v0.0` and `sceneops-canonical/v0.0`;
doing so would require new cross-dataset `ArtifactRecord` ownership
semantics this task deliberately does not introduce. Independent canonical
identity always wins over storage deduplication.

## Cross-dataset isolation

Because all three baselines derive from the same 10 real source scenes,
`canonical-verify`/`canonical-bootstrap` explicitly checks that no Scene id
is shared between `sceneops-scenes/v0.0` and `sceneops-canonical/v0.0`, and
no Episode id is shared between `sceneops-episodes/v0.0` and
`sceneops-canonical/v0.0` — proving the same external `scene-0061` produces
independently-owned canonical rows in each DatasetVersion, never a shared
or reassigned row (see `scripts/canonical/canonical_contract.sh`'s
`verify_cross_dataset_isolation`).

## What gets verified

Both commands cross-check the cached `DatasetVersion.scene.sceneCount`/
`episode.episodeCount` against live `GET /scenes`/`GET /episodes` membership
for every baseline — neither the cached summary nor the live count is
trusted alone; their equality is itself an asserted invariant (see
[`data-model.md` §2.0.1](../architecture/data-model.md#201-the-aggregate-summary-contract)
for the production contract this checks).

For each DatasetVersion, both commands check:

- **Scene domain** (when the contract expects `scene_count > 0`): live
  `GET /scenes` count and ownership, `.../quality` readiness/ground-truth,
  a registered `dataset_manifest` artifact. When the contract expects `0`,
  only the live count is checked (no Scene activity ever ran, per
  `SceneVersionSummary.is_unset()` — see that class's own docstring for why
  a `None`/unset summary does not by itself prove absence of data, and why
  this doc checks the live count instead).
- **Episode domain** (when the contract expects `episode_count > 0`): live
  `GET /episodes` count and ownership, exactly one
  `learning_data_export_manifest` artifact, an `episode_curation_manifest`
  artifact, and a byte-level check of the export manifest itself
  (`scripts/canonical/verify_learning_export.py`): `layout_version ==
  "v2-sharded"`, `episode_count`/`len(inputs)` match the contract,
  `base_export_id is None`, every shard's checksum resolves in MinIO, and
  shard row-count sums match the manifest's own `row_counts`. Shard counts
  are read from the manifest, never hardcoded — the production default
  shard policy (`max_episodes_per_shard=200`) makes a single shard for 10
  episodes virtually certain, but this baseline doesn't assume it.
- For `sceneops-canonical/v0.0` specifically: an actual
  `SceneOpsDataset.open()` reopen — `len(dataset) == 10`, and an
  independent read (metadata + ≥1 learning step + resolved
  observation/action feature values) of the first, middle, and last
  `EpisodeRef`.

## Regenerating after a spec change (v0.1+)

Never edit `canonical-v0.0.yaml`'s `scenes`/`datasets`/`source` sections
after `v0.0` has been materialized anywhere real. To change the baseline's
contents, add `config/baselines/canonical-v0.1.yaml` with `baseline_version:
v0.1` and its own dataset identities/version strings, then build the
equivalent bootstrap/verify commands against that new spec. This doc's
"Immutability" section is the reason: `v0.0` must stay a stable, comparable
reference across the whole team indefinitely.
