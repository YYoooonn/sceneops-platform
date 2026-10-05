# Reference corpus

> Developer / test input, not a Pipeline. Tool: [`tools/dataset-acquisition`](../../tools/dataset-acquisition/README.md).

A **reference corpus** is a versioned set of real source fixtures with one
acquisition / replay definition per fixture and a verified local cache of their
batch recordings. The corpus is `nuscenes-mini-v1`: all ten nuScenes v1.0-mini
scenes. A nuScenes source scene is an input selection; it is not a SceneOps
Scene.

```text
config/reference/nuscenes-mini-v1/corpus.json        hand-written definition      (Git)
config/reference/nuscenes-mini-v1/corpus.lock.json   generated fingerprints       (Git)
data/reference/nuscenes-mini-v1/recordings/          cached batch MCAPs           (local, git-ignored)
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
- `recording`: sha256, size, message count and per-topic counts of the batch MCAP.

For the whole lock, `tool` pins the versions that decide recording bytes (the
tool, `rosbags`, `mcap`, `zstandard`, `numpy`, `nuscenes-devkit`, Python
major.minor).

## Commands

```text
make reference-data-bootstrap [REFERENCE_SCOPE=smoke-1|nuscenes-mini-full-10] [UPDATE_LOCK=1]
make reference-data-verify    [REFERENCE_SCOPE=...]
```

- `bootstrap` fingerprints the source, materializes missing recordings
  (write-once: temporary file, fsync, atomic rename), verifies every fixture of the
  scope against the lock and checks each recording's L1 conformance with the
  publisher's `check`. Without `UPDATE_LOCK` it never changes the lock.
- `verify` runs the same checks and writes nothing.
- Neither starts or touches PostgreSQL, MinIO, Redis or Kafka: they run two
  one-shot containers with `--no-deps`.

A recording is reused only when the definition, the tool identity, the source
fingerprint and the recording's own sha256, size and counts agree with the lock.
Any disagreement fails and names the field. Nothing is regenerated, rewritten or
deleted; a stale or corrupt cache file is reported and left for the operator.
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
