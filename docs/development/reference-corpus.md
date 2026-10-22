# Reference corpus

> Developer / test input, not a Pipeline. Tool: [`integrations/dataset-acquisition`](../../integrations/dataset-acquisition/README.md).

A **reference corpus** is a versioned set of real source fixtures with one
acquisition / replay definition per fixture and a verified local cache of what is
prepared from each. The corpus is `nuscenes-mini-v1`: all ten nuScenes v1.0-mini
scenes. A nuScenes source scene is an input selection; it is not a SceneOps
Scene.

```text
reference fixture =
  sensor recording MCAP
  + source-derived ground-truth labels (when the source has them)
```

The source dataset is needed to prepare or re-prepare a fixture, never to consume
one: consumers read the recording and the labels through the lock and the cache.
Preparation (nuScenes -> MCAP) is source preparation; it is not an ingestion mode.
The two ingestion modes (Recording Import, Streaming Acquisition) and the 20
RobotRuns the corpus promotes are described in
[reference-contract.md](./reference-contract.md).

```text
config/reference/nuscenes-mini-v1/corpus.json        hand-written definition      (Git)
config/reference/nuscenes-mini-v1/corpus.lock.json   generated fingerprints       (Git)
data/reference/nuscenes-mini-v1/recordings/          cached locked MCAPs          (local, git-ignored)
data/reference/nuscenes-mini-v1/labels/              cached reference labels      (local, git-ignored)
```

## Scopes

| Scope | Fixtures | Use |
| --- | --- | --- |
| `smoke-1` (default) | `scene-0061` | a fixture selection: `scene-0061` of the full corpus, for fast checks and the journeys (it names no baseline of its own) |
| `nuscenes-mini-full-10` | all ten | the stable real-data input for benchmarks and release acceptance |

## Definition (`corpus.json`)

Corpus id, source format and version, shared `defaults` for `acquisition`
(channel groups) and `replay` (rate, subscriber wait), the fixtures
(`fixture_id`, `source_unit`, optional per-fixture overrides) and the scopes
(`["*"]` is every fixture). One resolved fixture definition drives both the locked
recording and the streaming replay. The file names no Scene, Episode or build
configuration; those stay in `config/baselines/`.

## Lock (`corpus.lock.json`)

Written only by `UPDATE_LOCK=1` (`reference prepare --update-lock`), reviewed in
the Git diff like `uv.lock`, never edited by hand. Per fixture:

- `definition_sha256`: the resolved definition;
- `source`: a content fingerprint of every input the fixture reads (the unit's
  table rows, the bytes of each converted image and point-cloud file, the CAN
  extracts), plus counts and total blob bytes;
- `recording`: sha256, size, message count and per-topic counts of the locked MCAP;
- `labels`: schema, sha256, size, sample count and label count of the reference
  label artifact (below).

For the whole lock, `tool` pins the versions that decide recording bytes (the
tool, `rosbags`, `mcap`, `zstandard`, `numpy`, `nuscenes-devkit`, Python
major.minor).

## Reference labels

Ground truth is not an acquisition fact, so it never enters the recording. Preparation
reads the source's annotations once and writes one **reference label artifact** per
fixture, `labels/<fixture>-<sha12>.labels.json` (write-once, content-addressed by the
first 12 hex digits of its sha256), schema `sceneops.reference_labels/1`:

```text
producer, producer_version, source_unit, anchor_topic, box_frame
samples[]            one per source key-frame sample, in sample order
  sample_token, anchor_timestamp_ns      the sample's LIDAR_TOP key frame (the label anchor)
  labels[]           label_id, instance_id, category, attributes, center_m, size_wlh_m, rotation_wxyz
```

A sample without objects stays in `samples`: it is coverage ("annotated as empty").
The artifact is baseline-neutral: it names no RobotRun, baseline, Scene or Episode.
It is canonical JSON (sorted keys, no whitespace), so equal source and tool give
equal bytes. The lock holds its sha256, size and counts; the cache file name follows
the sha256, so a changed artifact is never found under an old name.

`reference render-labels` turns the locked artifact and a target RobotRun id into a
`sceneops.label_set/v1` document for `IMPORT_LABELS`. It reads no source dataset, first
verifies the artifact against the lock, and is a deterministic function of (artifact,
`--robot-run-id`, `--label-set-id`): the same inputs give the same bytes, and the
RobotRun identity exists only in the rendered document. `make e2e-scene-ml` renders
into the runtime input area `data/inputs/labels/` (the worker's input root), through
the `reference-labels` service, which mounts no source dataset.

## Commands

```text
make reference-data-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10] [UPDATE_LOCK=1]
make reference-data-verify    [REFERENCE_SCOPE=...]
```

- `bootstrap` fingerprints the source, materializes missing recordings and label
  artifacts (write-once: temporary file, fsync, atomic rename), verifies every
  fixture of the scope against the lock and checks each recording's L1 conformance
  with the publisher's `check`. Without `UPDATE_LOCK` it never changes the lock.
- `verify` runs the same checks and writes nothing. It re-extracts the labels from
  the source and requires the locked artifact, so a changed annotation fails.
- Neither starts or touches PostgreSQL, MinIO, Redis or Kafka: they run two
  one-shot containers with `--no-deps`.

Consumers (`canonical-bootstrap`, `canonical-verify`) read the corpus through
`dataset-acquisition reference resolve`, which needs no source dataset: it checks
the lock against `corpus.json` and, without `--lock-only`, the cached recordings
(the tool identity, then each recording's bytes, size and counts), and prints each
fixture's locked facts and recording path. With `--with-labels` it also verifies
the cached label artifact and prints its entry and path; the canonical baseline
does not ask for labels, because it contains nothing derived. The canonical baseline
never converts source data; see [canonical-baseline.md](./canonical-baseline.md).

The streaming replay is the other consumer of a recording:
`dataset-acquisition reference replay --fixture <id>` (service `dataset-replay`, which
mounts the corpus and the cache read-only) verifies one fixture's cached recording
against the lock and publishes that MCAP on ROS 2 topics through the replay sink. It
reads no source dataset and does not compare the tool identity, since the replay image
is not the interpreter that wrote the recording; the recording's sha256, size and counts
are what it relies on. The locked recording is the one runtime replay source: there is no
replay of the raw dataset. `make streaming-bootstrap` replays every fixture of a scope this
way into the persistent streaming baseline ([canonical-baseline.md](./canonical-baseline.md)). The streaming replay container mounts no raw dataset (the run probes that none is visible).
`make e2e-streaming-equivalence` compares a fixture's registered streamed acquisition with the same
locked recording's Recording Import RobotRun, reading both from the ArtifactStore.

A recording is reused only when the definition, the tool identity, the source
fingerprint and the recording's own sha256, size and counts agree with the lock; a
label artifact only when its own sha256, size and counts do (and, in preparation,
when the source still produces it). Any disagreement fails and names the field.
Nothing is regenerated, rewritten or deleted; a stale or corrupt cache file is
reported and left for the operator.
The cache file name carries a key of source, acquisition, tool and source
fingerprint, so a changed input is never found under an old name. Superseded
files are not pruned.

Changing the definition, the source or the tool makes `verify` fail until the
change is deliberately locked with `UPDATE_LOCK=1`. A changed tool identity must
re-lock every fixture of the corpus.

## Pinned raw sources

Some sources enter SceneOps as preserved native files rather than as an acquisition
recording (Dataset Integration, [ADR-015](../adr/015-multi-source-platform.md)). Their
raw copy is pinned the same way the corpus is: a hand-written definition and a
generated lock in Git, and a local, git-ignored copy that is checked against the lock.

```text
config/reference/lerobot-pusht/source.json        hand-written definition      (Git)
config/reference/lerobot-pusht/source.lock.json   generated checksum lock      (Git)
data/raw/lerobot/pusht/                           the pinned copy              (local, git-ignored)
```

The definition names the upstream (`lerobot/pusht` on Hugging Face), its full commit
revision, the include patterns (`README.md`, `data/**`, `meta/**`, `videos/**`) and the
local root. The lock lists every selected file with its path, size and sha256, plus the
checksum the upstream publishes for it: the LFS sha256 of an LFS file, the git blob sha1
of any other file. A download is accepted only when it agrees with both, so the lock is
evidence of the upstream bytes, not a record of whatever was downloaded. pusht at
`7628202a2180972f291ba1bc6723834921e72c19` is 7 files and 7,684,382 bytes.

```text
make reference-source-fetch  [SOURCE=lerobot-pusht] [UPDATE_LOCK=1]
make reference-source-verify [SOURCE=lerobot-pusht]
```

- `fetch` downloads every locked file missing from the local root at the pinned
  revision, verifies it against the lock and writes it with a temporary file, fsync
  and an atomic rename. Then it verifies the whole local tree. A present file is never
  overwritten. With `UPDATE_LOCK=1` it first re-lists the upstream at the pinned
  revision, applies the include patterns and rewrites the lock; nothing else writes it.
- `verify` runs offline and writes nothing. It fails on a changed, missing or
  unexpected local file (a stray `.DS_Store` included) and on a definition that
  changed since the lock was written.
- `tools/reference/raw_source.py` uses the Python standard library only and imports
  no SceneOps package, so its checksums are independent expectations for the tests
  that read the pinned copy (`packages/sceneops-sources/tests/test_pusht_preservation.py`
  skips, naming the command, when the copy is absent). No container or stack is
  involved; only `fetch` uses the network.

### Reference source snapshots

`config/reference/source-snapshots.json` declares the snapshots the platform is accepted
on (`make test-infrastructure SUITE=sources`): for each, the local root, the declaration
(`format`, `upstream`, `upstream_revision`, `include`) and its annotations.

| Snapshot | Declaration | Selected |
| --- | --- | --- |
| `lerobot-pusht` | `lerobot`, `lerobot/pusht`, the pinned commit, the raw-source pin's include patterns, `license=mit` | the 7 locked files, 7,684,382 bytes |
| `nuscenes-scene-0061` | `nuscenes`, `nuscenes`, `v1.0-mini`; `LICENSE`, `can_bus/scene-0061_*`, `maps/*`, `samples/*/n015-2018-07-24-11-22-45+0800__*`, `sweeps/*/n015-2018-07-24-11-22-45+0800__*`, `v1.0-mini/*`; `license=nuscenes-terms-of-use` | 2,989 files, 519,531,462 bytes: the tables, maps and licence, the readings of scene-0061's log (the only scene of that log) and its 8 CAN extracts |

The pusht entry restates its raw-source pin (`tools/dev/tests/test_capacity_preflight.py`
keeps them equal). The nuScenes slice is read from the local v1.0-mini tree, which has no
lock; the acceptance suite derives its expectations by walking and hashing that tree.
The slice is its own content, so its ids differ from those of a full v1.0-mini snapshot.

## Limitations

- The lock pins the tool's package versions, not the host. Byte-identical
  recordings were checked on one machine (same pinned image, two clean outputs);
  they are not claimed across CPU architectures or hosts.
- `corpus.json` supports the `nuscenes` source format only.
- `data/reference` is not touched by `make local-reset`; remove it by hand to
  reclaim the space (about 3.8 GiB for the full corpus).
