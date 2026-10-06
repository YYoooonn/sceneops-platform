# Golden reference contract

> Developer / test orchestration, not a Pipeline. Decision record:
> [ADR-007](../adr/007-canonical-ingestion-architecture.md) §35. Inputs: the
> [reference corpus](./reference-corpus.md) and the two baselines of
> [canonical-baseline.md](./canonical-baseline.md).

The golden reference contract names the RobotRuns that are the platform's
reference state. They are reference infrastructure, not test output: they persist
across journeys, are never rebuilt by one, and are identified by a fixed identity,
never by a timestamp or a random suffix.

```text
nuScenes mini reference corpus              10 fixtures
  │
  ├─ Recording Import        existing MCAP → RobotRun            10 RobotRuns
  └─ Streaming Acquisition   ROS 2 → Kafka → capture → RobotRun  10 RobotRuns

  Golden reference contract                  20 RobotRuns
                                             20 whole-recording Scenes
                                             20 Episodes
```

## Terminology

| Term | Meaning |
| --- | --- |
| Source preparation | nuScenes → locked MCAP (`make reference-data-bootstrap`). It produces the corpus; it is not an ingestion mode. |
| Recording Import | an existing MCAP → RobotRun (`recording_import`; the `ref-<scope>` baseline, `make canonical-bootstrap`). |
| Streaming Acquisition | ROS 2 → Kafka → capture → RobotRun (`streaming_acquisition`; the `stream-ref-<scope>` baseline, `make streaming-bootstrap`). |
| RobotRun boundary | the immutable recording and its manifest; both modes end here, and everything above it is the same canonical platform. |
| Canonical platform | RobotRun → Scenes → Episodes → derived layers. |

## Roles

| Side | Role |
| --- | --- |
| 10 Recording Import RobotRuns | the primary stable platform / ML reference baseline: each pins the locked recording byte for byte |
| 10 Streaming Acquisition RobotRuns | the acquisition / transport-equivalence reference baseline: each pins its own capture, whose messages and per-channel counts equal the lock's |
| all 20 | the acquisition boundary contract: the same corpus reaches the same canonical state through both modes |

## The contract file

`config/reference/nuscenes-mini-v1/reference_contract.json`, schema
`sceneops.reference_contract/1`, versioned by `contract_version`.

| Field | Content |
| --- | --- |
| `corpus` | `corpus_id` and `scope` the contract promotes; the corpus definition and `corpus.lock.json` are referenced, not copied |
| `fixtures[]` | per fixture: `fixture_id`, and the facts it must still have in the lock (`recording_sha256`, `message_count`, `channel_count`); per-channel counts are read from the lock |
| `ingestion_modes.<mode>` | `role`, `baseline_id`, `robot_id`, `dataset_id`, `dataset_version`, `run_id_template`, `recording` (`locked` or `captured`), `capture_source` (the registered manifest's source), and for streaming `transport` |
| `units` | `scene_policy`, `scene_unit_key`, `scenes_per_robot_run`, `episodes_per_robot_run` |
| `expected_totals` | `fixtures`, `robot_runs`, `scenes`, `episodes` |

The Scene policy, the per-RobotRun counts and the totals are stated here and only
here. `validate` cross-checks them against `config/baselines/` (the Scene
build configuration's segmentation policy and `baseline_shape.json`), the lock's
facts, the corpus scope, and the totals the contract itself implies.

### Identities

`run_id_template` is `run-{baseline_id}-{fixture_id}`, the identity rule of the
baselines themselves, so the contract and the bootstrap scripts agree by
construction. For `nuscenes-mini-full-10`:

| Mode | RobotRun | Robot | DatasetVersion |
| --- | --- | --- | --- |
| `recording_import` | `run-ref-nuscenes-mini-full-10-<fixture>` | `robot-ref-nuscenes-mini-full-10` | `sceneops-ref-nuscenes-mini-full-10` / `baseline` |
| `streaming_acquisition` | `run-stream-ref-nuscenes-mini-full-10-<fixture>` | `robot-stream-ref-nuscenes-mini-full-10` | `sceneops-stream-ref-nuscenes-mini-full-10` / `baseline` |

`<fixture>` ranges over `scene-0061`, `scene-0103`, `scene-0553`, `scene-0655`,
`scene-0757`, `scene-0796`, `scene-0916`, `scene-1077`, `scene-1094`, `scene-1100`.
There is no smoke baseline: `REFERENCE_SCOPE=smoke-1` selects `scene-0061` of the
contract's baselines and creates no RobotRun or DatasetVersion of its own. A RobotRun
of a test journey can never satisfy the contract: membership is the exact identity,
under the contract's robot.

## Commands

```text
make reference-contract-verify      read-only
make reference-contract-bootstrap   converge on the contract
make reference-contract-verify REQUIRE_PRISTINE=1    ... and nothing but the contract
python3 scripts/reference/reference_contract.py show|validate|verify|bootstrap [--require-pristine]
python3 scripts/reference/reference_contract.py pair [--fixture <id> | --scope <scope>]
python3 scripts/reference/reference_contract.py fingerprint
```

Both print one JSON report on stdout (`sceneops.reference_contract_report/1`);
progress and violations go to stderr. The exit code is non-zero on any violation.

### `reference-contract-verify`

Read-only. It validates the contract against its sources, then:

1. runs the existing read-only verifiers of both baselines
   (`canonical-verify`, `streaming-verify`) with the contract's identity, so
   membership, manifest pins, readiness (validated, profiled, `ready` at the
   current revision) and DatasetVersion summaries have one implementation;
2. reads every contract RobotRun's registered manifest and requires its capture
   source, robot, recording (locked for Recording Import, a capture for Streaming
   Acquisition), message count and per-channel counts to equal the lock's;
3. evaluates the contract itself: all 20 identities registered under the contract's
   robots; no other RobotRun on a contract robot; no fixture/mode pair missing,
   duplicated or mapped to another run; per RobotRun exactly the contract's Scenes
   (unit key `recording` for `whole_recording`) and Episodes; no Scene or Episode
   of a contract DatasetVersion that belongs to another RobotRun; observed totals
   equal `expected_totals`.

By default it does not require the platform to hold exactly the contract's RobotRuns
(a general development platform may hold any), and the contract stays valid while the
fixed derived test datasets of the `REFERENCE_DERIVED` workflows exist beside it. State
outside the contract is reported in three separate groups and never fails the default
check:

| Group | Meaning |
| --- | --- |
| non-contract RobotRuns | any RobotRun not named by the contract (by kind: `temporary_e2e`, `reference_like_baseline`, `unclassified`; a naming heuristic for reporting only) |
| derived test datasets | Datasets in the reserved `sceneops-test-` namespace: the fixed, test-owned state of the derived journeys and infrastructure tests ([test-matrix.md](./test-matrix.md#test-state-classes)), with the number of Scenes and Episodes they hold |
| foreign datasets | any other non-contract Dataset |

The report's `state` is the compact check (`contract_robot_runs`, `non_contract_robot_runs`,
`contract_scenes`, `contract_episodes`, `derived_test_datasets`, `foreign_datasets`,
`pristine`), and its `inventory` lists the contract RobotRuns and every non-contract one,
the three dataset groups, the Scenes and Episodes outside the contract DatasetVersions,
and orphan Scenes and Episodes.

`REQUIRE_PRISTINE=1` (`--require-pristine`) is the check of a freshly reset reference
environment: every non-contract RobotRun (`non_contract_robot_run`), derived test dataset
(`derived_test_dataset`) and foreign dataset (`foreign_dataset`) is then a violation.
It is the only flag that treats derived test state as a problem; the contract's own
validity does not depend on it.

### `pair` and `fingerprint`

Two read-only helpers for workflows that consume the contract.

`pair` resolves one fixture (`--fixture`, or a `--scope` that selects exactly one:
`smoke-1` is `scene-0061`) to its two RobotRuns, with their robot and DatasetVersion
per ingestion mode and the lock's recording facts (sha256, size, message count,
per-channel counts). It reads no platform state. `make e2e-streaming-equivalence`
takes both identities from it, so no journey states a RobotRun id of its own.

`fingerprint` prints one JSON line with every RobotRun, Dataset, Scene and Episode the
platform holds, through FastAPI: identities plus the registration, manifest-checksum and
update fields that change when a record is rewritten. Two fingerprints are equal exactly
when no record was added, removed or changed; a workflow that must create no state
compares one taken before and after.

### `reference-contract-bootstrap`

Composes the existing bootstraps; it implements no ingestion itself.

```text
validate the contract
→ read the platform; stop on a conflicting identity           (nothing changed)
→ make canonical-bootstrap  BASELINE_ID=<recording_import baseline>
→ make streaming-bootstrap  BASELINE_ID=<streaming_acquisition baseline>
→ collect and evaluate as `verify`; compare record fingerprints before and after
```

| State | Outcome |
| --- | --- |
| complete and consistent | reused: no publication, replay, registration or build; the cached recordings are not re-hashed and Kafka is not started |
| RobotRun missing | created through its mode's supported path |
| RobotRun registered, Scene or Episode missing or not ready | converged on the registered revisions |
| streaming capture finalized, published or registration pending | recovered by `publish-pending` / `reconcile --apply`, not replayed |
| contract identity under another robot; foreign RobotRun on a contract robot; foreign Scene / Episode in a contract DatasetVersion | fails before anything is changed |
| registered recording differs from the lock | the baseline bootstrap fails loudly; the RobotRun is never modified |

The contract's `baseline_id` is always passed on the command line of the
sub-commands, overriding any `BASELINE_ID` in the environment, and `FIXTURE` is
cleared, so no timestamp, random or single-fixture identity can be created through
this entry point. The report's `bootstrap` section lists the records reused,
created, and any that changed or vanished; an immutable RobotRun or Scene / Episode
record that changed during the run is a violation.

## The reference environment

The reference environment is a local runtime that holds exactly the contract.
Generated runtime state is disposable and nothing deletes part of it; the environment
is reconstructed from the locked corpus, which `local-reset` keeps:

```text
make local-reset                              PostgreSQL, Redis, MinIO, Kafka log, capture volume dropped
make reference-data-verify REFERENCE_SCOPE=nuscenes-mini-full-10     the locked corpus is intact
make reference-contract-bootstrap REQUIRE_PRISTINE=1                 20 RobotRuns, 20 Scenes, 20 Episodes
make reference-contract-verify REQUIRE_PRISTINE=1                    the state check, read-only
```

A workflow that registers RobotRuns of its own (`MUTATING_ACQUISITION`) leaves
non-contract state that nothing removes, so it runs only on a runtime declared
disposable and the reference environment is rebuilt afterwards. The workflows that only
consume the contract (`REFERENCE_DERIVED`) register no RobotRun and write into fixed
`sceneops-test-*` Datasets of their own that a repeated run reuses; `reference-contract-verify`
reports them as derived test datasets and `REQUIRE_PRISTINE=1` fails on them until the next
reset. Those that create nothing (`REFERENCE_READ_ONLY`, `e2e-streaming-equivalence`) leave
the environment exactly as it was. See [test-matrix.md](./test-matrix.md#test-state-classes)
and [ADR-007](../adr/007-canonical-ingestion-architecture.md) §35.3, §36.7.

## Refreshing the contract

A change that alters a contract RobotRun's identity or content (a new lock, a new
Scene build configuration, a changed capture format) makes `verify` fail
(`lock_mismatch`, `config_mismatch`, a baseline verifier failure). There is no
implicit refresh: the bootstrap never replaces a registered RobotRun, and a changed
producer fails at registration. A refresh is a deliberate, separate operation:
a new `contract_version` (new `baseline_id`s, so the old RobotRuns stay immutable
and verifiable until removed), built by this bootstrap, with the previous baselines
removed by an explicit reset. No such operation exists yet.

## Limitations

- The contract is one corpus and one scope (`nuscenes-mini-full-10`), with whole-recording
  Scenes and one Episode per recording. The platform itself has no such restriction;
  the restriction lives only in the contract and the baseline build configurations.
- `verify` runs both baseline verifiers and a manifest read through a one-shot
  container; it takes about a minute on the local stack.
- The inventory reports platform state reachable through FastAPI. Orphan objects in
  the ArtifactStore are classified by `make artifact-lifecycle-once`, not here.
- The contract does not isolate the mutating workflows from the reference runtime: they
  are guarded (`DISPOSABLE_RUNTIME=1`), not run on a runtime of their own.
- A reconstruction from an empty runtime replays every fixture through ROS 2 and Kafka
  and builds 40 Scenes / Episodes; it takes as long as that, once.
