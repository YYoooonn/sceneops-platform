# Reference corpus

> Developer / test input, not a Pipeline. Tool: [`tools/dataset-acquisition`](../../tools/dataset-acquisition/README.md).

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

```text
config/reference/nuscenes-mini-v1/corpus.json        hand-written definition      (Git)
config/reference/nuscenes-mini-v1/corpus.lock.json   generated fingerprints       (Git)
data/reference/nuscenes-mini-v1/recordings/          cached batch MCAPs           (local, git-ignored)
data/reference/nuscenes-mini-v1/labels/              cached reference labels      (local, git-ignored)
```

## Scopes

| Scope | Fixtures | Use |
| --- | --- | --- |
| `smoke-1` (default) | `scene-0061` | fast checks |
| `nuscenes-mini-full-10` | all ten | the stable real-data input for benchmarks and release acceptance |

## Definition (`corpus.json`)

Corpus id, source format and version, shared `defaults` for `acquisition`
(channel groups) and `replay` (rate, subscriber wait), the fixtures
(`fixture_id`, `source_unit`, optional per-fixture overrides) and the scopes
(`["*"]` is every fixture). One resolved fixture definition drives both the batch
recording and the streaming replay. The file names no Scene, Episode or build
configuration; those stay in `config/baselines/`.

## Lock (`corpus.lock.json`)

Written only by `UPDATE_LOCK=1` (`reference prepare --update-lock`), reviewed in
the Git diff like `uv.lock`, never edited by hand. Per fixture:

- `definition_sha256`: the resolved definition;
- `source`: a content fingerprint of every input the fixture reads (the unit's
  table rows, the bytes of each converted image and point-cloud file, the CAN
  extracts), plus counts and total blob bytes;
- `recording`: sha256, size, message count and per-topic counts of the batch MCAP;
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
replay of the raw dataset. `make e2e-streaming-equivalence` replays a fixture with the replay
container's raw-dataset mount shadowed by an empty volume and compares the streamed
acquisition with the same locked recording's batch baseline.

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

## Limitations

- The lock pins the tool's package versions, not the host. Byte-identical
  recordings were checked on one machine (same pinned image, two clean outputs);
  they are not claimed across CPU architectures or hosts.
- `corpus.json` supports the `nuscenes` source format only.
- `data/reference` is not touched by `make local-reset`; remove it by hand to
  reclaim the space (about 3.8 GiB for the full corpus).
