# ADR-016: Logical URIs and verified payload access

## Status

Accepted. This ADR adds the access layer only. Canonical manifests still carry the
[ADR-015](./015-multi-source-platform.md) §6.5 `PayloadRef` union (`ArtifactPayloadRef` |
`SourceFileRef`) unchanged. No manifest, revision, object key, stored byte or database
record changes.

**Amended (2026-10-29, R2).** Camera images sent to the GroundingDINO server are verified
by the server against the sample's pin ([Detection camera images](#detection-camera-images)).
The "External image serving" limit below describes the contract before this amendment.

**Amended (2026-10-29, R3).** The unified `PayloadRef` and the manifest versions that
carry it are decided in [ADR-017](./017-unified-payload-ref-and-versioned-manifests.md).
A `PayloadRef` is a `PinnedPayload` and is read by `VerifiedPayloadReader` unchanged.
`PayloadLocator.pins` still bridges only the legacy references and refuses a
`PayloadRef` until the registration gate checks one.

**Amended (2026-10-30, R4).** Manifests now hold `PayloadRef` only, so a consumer reads
the manifest's reference as it is and `PayloadLocator` is removed; a whole-object
consumer refuses a selector (`require_whole_payloads`). The Logical URI grammar,
namespaces, resolver and verified reader are unchanged
([ADR-017](./017-unified-payload-ref-and-versioned-manifests.md), R4 cutover).

## Context

Canonical payload bytes come from two places:

- recording-derived payloads, which are `OBSERVATION_PAYLOAD` artifacts at
  `{observation_payload_root}/{robot_run_id}/{artifact_id}`;
- preserved dataset files, which sit at `{sources_root}/contents/{content_id}/files/{path}`
  and are listed in their SourceContent inventory.

Both kinds of reference already pin sha256 and size. Readers, however, received only a
physical URI from `PayloadLocator.uris`, and each reader decided for itself what to verify.
Detection LiDAR used `ArtifactStore.read_bytes` on that URI and decoded the result without
checking the checksum. A corrupted object of the right size, or one that still parsed,
could therefore become lifted 3-D boxes.

The two kinds also lacked a shared name for "these bytes" that did not depend on bucket,
endpoint or backend. A later unified `PayloadRef` needs such a name, and so does a single
read path that verifies the bytes.

## Decision

### Logical URI grammar

```text
sceneops://<namespace>/<segment>(/<segment>)*

namespace   observation-payloads | sources        fixed tokens, never renamed or reused
segment     1*( unreserved | "%" HEXUPPER HEXUPPER )
unreserved  A-Z a-z 0-9 - . _ ~                   (RFC 3986 §2.3)
```

- The key is the object key relative to the namespace's storage root, for example
  `run-001/payload-…` or `contents/content-…/files/samples/LIDAR_TOP/x.pcd.bin`. No
  bucket, endpoint, backend or deployment prefix appears in the URI.
- Each segment is percent-encoded over its UTF-8 bytes. Every byte outside the unreserved
  set is written as uppercase `%XX`, so `+`, `?`, `#`, `%`, space and non-ASCII characters
  are always escaped. A raw `?` or `#` is never part of a Logical URI.
- Every key has exactly one spelling. A parser accepts only that spelling: no lowercase
  hex, no escaped unreserved character, no malformed escape, no invalid UTF-8. It decodes
  once (`%252E` names the literal segment `%2E`) and never normalizes. Unicode NFC and
  NFD spellings are different keys.
- A key has no empty, `.` or `..` segment, no encoded `/` (`%2F`), no backslash and no
  control character. Such a key is refused rather than rewritten. This rule is the
  object-key form of `validate_source_path`.

Implemented in `sceneops_core.artifacts.logical_uri`. That module also defines
`PinnedPayload`, the read contract (`uri`, `checksum`, `size_bytes`), and
`LogicalPayloadPin`, which adds `media_type` and validates every field.

### Resolution

`sceneops_storage.PayloadUriResolver` maps each namespace to one configured root.
`from_settings` takes `ArtifactSettings.observation_payload_root_uri` and
`ArtifactSettings.source_root_uri`, which are the roots the producers write under.

- `resolve(uri)` returns `{root}/{key}`, with the key appended verbatim. This matches the
  key the producers built with `join_uri`. The backend's own parsing then takes over; the
  local and S3 backends already keep `?` and `#` in keys verbatim.
- `logical_uri(physical)` is the reverse. It accepts only `{root}/{valid key}` for a
  configured root. Matching is by whole path segment, so `…/sources-old/x` does not match
  `…/sources`. Roots that are equal or nested are rejected when the resolver is built, so
  each physical URI has at most one Logical URI.
- Resolution never reads a database, never interprets a key as a Source, Scene or
  RobotRun, and never copies, moves or rewrites bytes. Unknown namespaces, unconfigured
  namespaces and unsafe keys are refused.

### Verified reads

`sceneops_storage.VerifiedPayloadReader.read(pin)` works in five steps: resolve the URI,
read the whole object through the existing `ArtifactStore`, check the byte length, check
the full-object sha256, and return the bytes. It raises `PayloadMissingError` if there is
no object and `PayloadVerificationError` on a size or checksum mismatch. It never falls
back to a metadata-only check. An empty object is valid when its pin says size 0 with the
sha256 of zero bytes.

The registration gates are unchanged. They check object presence and listed size
(`verify_payload_objects`, `verify_source_file_refs`): payload bytes were hashed when they
were published and content files when the content was first registered, and both are
write-once. The reader is the byte-level check on every pinned read. The two gates prove
different things, and neither replaces the other.

### Legacy bridge

`PayloadLocator.pins(refs)` turns each existing reference into a `LogicalPayloadPin`. It
is still the one place downstream of the manifests that dispatches on reference kind
(MS-10).

- `ArtifactPayloadRef`: the existing checks still apply. The record must exist, be an
  `OBSERVATION_PAYLOAD`, and record exactly the referenced checksum, size and media type.
  The bridge also requires the record to be owned by a RobotRun. It derives the Logical
  URI from the record's stored URI, which must lie under the observation-payloads root.
  The resulting key must equal the producer's layout `{owner_id}/{artifact_id}`
  (`observation_payload_key`), and the Logical URI must resolve back to the record's URI.
- `SourceFileRef`: the existing checks still apply. The content must be registered, its
  inventory ArtifactRecord must be a stored inventory with the pinned `content_checksum`,
  and the inventory entry for the path must carry exactly the referenced sha256 and size.
  The inventory URI must name the referenced content. The Logical URI is derived from the
  preserved file's location (`content_file_uri`, D4), which must lie under the sources
  root and must resolve back to that location. The media type is still the builder's
  assertion.
- If the stored location and the expected layout or configured roots disagree, the bridge
  fails. It never infers a different path.
- A `SourceFileRef` with a `frame` selector is refused (`UnsupportedPayloadSelectorError`)
  before any lookup. Handing a consumer the whole MP4 in place of one frame would be
  silently wrong.

`PayloadLocator.uris` now returns `physical_uri(pin)` for each pin, so consumers that
still need a location go through the same checks.

### Detection LiDAR

`DetectionSampleInput.lidar_payload` carries the LiDAR pin. This field replaces
`lidar_uri`, and it must be set if and only if a LiDAR observation is set. GroundingDINO
reads the LiDAR only through `DetectionInferenceRequest.payload_reader`. A missing or
corrupt payload becomes the existing per-sample result: predictions with
`lifting_status = "failed"` and the reader's error as `lifting_error`. Valid payloads
decode exactly as before. Metric definitions do not change.

### Detection camera images

`DetectionSampleInput.camera_payload` carries the camera pin; the sample holds no image
location. The GroundingDINO client resolves the pin's Logical URI to a physical URI only
when it builds the HTTP request, and `POST /v1/detect` carries `image_uri` together with
the pin's `image_checksum` (`sha256:<64 lowercase hex>`, of the whole stored object) and
`image_size_bytes`. Both are required; the endpoint has no unpinned form. The server sees
a `file://` or `s3://` location and a checksum, never a Logical URI, a content or RobotRun
id, or a source kind, so Recording and native-dataset images use the same request.

The server checks the location against its existing rules (allowed file roots, configured
S3 client, no other scheme), reads the object once (at most one byte past the pinned
size), compares length and sha256, and decodes those same bytes from memory. It never
reopens the location after the check, so a change to the object cannot reach the model.
The model is not invoked for an image that fails. Failures are coded in `detail.code`:
`image_not_found`, `image_integrity_mismatch`, `image_decode_failed` (422),
`image_access_denied` (403), `invalid_image_uri` (422); model errors stay 500.

The client raises `PayloadMissingError`, `PayloadVerificationError` or `ImageDecodeError`
for the first three, and `httpx.HTTPStatusError` for anything else, without retrying. As
before, a failed request fails the run and nothing is published. A camera failure
removes a sample's 2-D detections entirely, and the prediction manifest cannot express a
sample that was not inferred, so the per-sample treatment used for LiDAR does not apply.
Metric definitions do not change.

## Guarantees and limits

- **Immutability.** Each key under both namespaces names write-once content: a
  deterministic payload id, or a content id derived from the bytes. A Logical URI
  therefore always names the same bytes, and its pin's checksum detects any change.
- **Supported storage migration.** Logical URIs and pins stay valid when the root of a
  namespace moves to another bucket, endpoint, prefix or backend, provided every object
  keeps its key relative to the root. Only the resolver configuration changes.
- **Not supported.** Changing object keys themselves requires an explicit mapping or a
  migration; no key registry exists. The bridge also derives the Logical URI from each
  record's stored physical URI. A relocation therefore also needs those records updated,
  or the bridge refuses them, until manifests carry Logical URIs directly.
- **Not covered by this decision.** Unified `PayloadRef` and manifest schema changes,
  video frame decoding, streaming or range reads, caching, and a blob registry.
- **External image serving** (before the R2 amendment). The GroundingDINO server still
  receives the camera image's physical URI (`image_uri`) and reads those bytes itself,
  without a checksum. Camera bytes sent to the model are therefore not verified, and only
  file URIs on a shared volume reach it. This contract needs its own change.

## Alternatives considered

- **Raw path characters in the URI** (`sceneops://sources/a+b?c`). Rejected: `?` and `#`
  would read as query and fragment, and `+` is decoded as a space by some form decoders.
- **Database-backed URI or blob registry.** Rejected for now. Keys are already
  deterministic and write-once, and roots are configuration. A registry would add a
  second source of truth with no current need.
- **Verifying LiDAR at sample resolution in the worker.** Rejected: it would read every
  payload twice, and a backend could still read around the check. The backend reads
  through the reader instead, and the request carries no unverified path.

## Consequences

- Both payload sources resolve through one grammar, one resolver and one verified reader.
- The source-dispatch check now lists `PayloadLocator.pins` as the locator's dispatch site.
- A future unified `PayloadRef` can carry a Logical URI plus checksum, size, media type
  and selector, and reuse this reader unchanged.
