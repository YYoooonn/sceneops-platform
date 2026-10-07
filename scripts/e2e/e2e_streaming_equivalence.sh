#!/usr/bin/env bash
# e2e_streaming_equivalence.sh — transport-preservation acceptance over the golden
# reference contract (ADR-007 §29.12, I-35).
#
# One logical source — the LOCKED REFERENCE MCAP of one corpus fixture — is in the
# platform twice, as two RobotRuns of the golden reference contract
# (docs/development/reference-contract.md):
#
#   Recording Import        the locked MCAP -> publish -> RobotRun -> Scene / Episode
#   Streaming Acquisition   the locked MCAP -> replay -> ROS 2 -> bridge -> Kafka
#                           -> capture -> publish-pending -> reconcile -> RobotRun
#                           -> Scene / Episode
#
# This journey proves, read-only, that the two are the same acquisition:
#
#   identity         each RobotRun pins its own registered recording; the import is the
#                    locked recording, the stream a different recording captured from
#                    Kafka; both carry the locked message and per-channel counts
#   acquisition      same channels, message types and encodings, per-channel payload
#                    sequences and counts, source observation times, /tf_static
#                    (§29.12); container bytes, capture log_time, schema text and
#                    cross-channel write order are not compared
#   canonical        every Scene's and Episode's semantic content is equal (I-35),
#                    with provenance that really differs
#   negative control the verifier detects a dropped message, a 1 ns source-time shift,
#                    a changed payload checksum, a Scene and an Episode semantic change
#
# Both recordings are read from the ArtifactStore (the RobotRuns' registered
# recordings and manifests) by streaming_equivalence_verify.py in a one-shot
# recording-publisher container, checked against their registered checksums, held
# in that container's temporary directory and removed with it. Nothing is replayed,
# captured, published, registered or built; Kafka, ROS 2, the bridge, the replay
# container, the capture volume and the reference cache are not involved. The
# Kafka lifecycle records of a streamed run (RUN_START, N telemetry records,
# RUN_END, and the receipt's offsets over them) are proven against a real broker in
# ros2/capture/tests/test_lifecycle_integration.py.
#
# Test-state class: REFERENCE_READ_ONLY (docs/development/test-matrix.md). It writes
# no PostgreSQL row, no MinIO object, no DatasetVersion and no RobotRun, so it runs
# on the reference environment and needs no DISPOSABLE_RUNTIME. The journey
# fingerprints every RobotRun, Dataset, Scene and Episode before and after and fails
# if anything differs.
#
# Platform reads go through FastAPI; the host needs Docker Compose, curl, jq and
# python3 (standard library only, for the contract).
#
# Prerequisites: `make local-up`, and the golden reference contract's RobotRuns for
# the fixture (`make reference-contract-bootstrap`; checked by `make
# reference-contract-verify`).
#
# Usage:
#   make e2e-streaming-equivalence                       # smoke-1 (scene-0061)
#   make e2e-streaming-equivalence SCENE=scene-0103      # one named fixture

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
export API_BASE_URL ENV_FILE

# Selection is the corpus's own: a fixture (SOURCE_UNIT / FIXTURE) or a scope that
# selects exactly one (smoke-1 = scene-0061). Identities come from the contract.
FIXTURE="${FIXTURE:-${SOURCE_UNIT:-}}"
REFERENCE_SCOPE="${REFERENCE_SCOPE:-smoke-1}"
CONTRACT_TOOL=(python3 "$REPO_ROOT/scripts/reference/reference_contract.py")
VERIFIER=/workspace/e2e/streaming_equivalence_verify.py

artifact_field() { # <artifact-id> <jq-field> -> a field of the ArtifactRecord
  api_get "$API_BASE_URL" "/artifacts/$1" | jq -r ".artifact.$2"
}

manifest_uris() { # <scenes|episodes> <dataset-id> <version> <robot-run-id> -> JSON array of manifest URIs
  local ids id
  ids="$(api_get "$API_BASE_URL" "/$1?dataset_id=$2&dataset_version=$3&limit=500" \
    | jq -r --arg r "$4" "[.$1[] | select(.robotRunId == \$r) | .manifestArtifactId] | sort | .[]")"
  for id in $ids; do artifact_field "$id" uri; done | jq -R . | jq -sc .
}

# arm_request <mode-json> -> the verifier's description of one registered RobotRun:
# its manifest, its recording (as registered) and its Scene / Episode manifests.
arm_request() {
  local arm="$1" run_id robot_id dataset version run
  run_id="$(echo "$arm" | jq -r '.robot_run_id')"
  robot_id="$(echo "$arm" | jq -r '.robot_id')"
  dataset="$(echo "$arm" | jq -r '.dataset_id')"
  version="$(echo "$arm" | jq -r '.dataset_version')"
  run="$(api_get "$API_BASE_URL" "/robot-runs/$run_id" | jq -c '.robotRun')" \
    || fail "RobotRun $run_id is not registered (make reference-contract-bootstrap)"
  [ "$(echo "$run" | jq -r '.robotId')" = "$robot_id" ] \
    || fail "RobotRun $run_id is registered under $(echo "$run" | jq -r '.robotId'), the contract's robot is $robot_id"
  jq -cn --arg id "$run_id" \
    --arg manifest "$(artifact_field "$(echo "$run" | jq -r '.manifestArtifactId')" uri)" \
    --argjson recording "$(api_get "$API_BASE_URL" "/artifacts/$(echo "$run" | jq -r '.recordingArtifactId')" \
      | jq -c '.artifact | {uri, checksum, size_bytes: .sizeBytes}')" \
    --argjson scenes "$(manifest_uris scenes "$dataset" "$version" "$run_id")" \
    --argjson episodes "$(manifest_uris episodes "$dataset" "$version" "$run_id")" \
    '{robot_run_id: $id, manifest_uri: $manifest, recording: $recording,
      scene_manifest_uris: $scenes, episode_manifest_uris: $episodes}'
}

echo "=== [0/5] control plane (no Kafka, ROS 2, bridge, replay or capture involved) ==="
require_api "$API_BASE_URL"
echo "  ✅  API healthy"
echo ""

echo "=== [1/5] the fixture: both golden RobotRuns resolved from the reference contract ==="
if [ -n "$FIXTURE" ]; then SELECTION=(--fixture "$FIXTURE"); else SELECTION=(--scope "$REFERENCE_SCOPE"); fi
PAIR="$("${CONTRACT_TOOL[@]}" pair "${SELECTION[@]}")" || fail "could not resolve a fixture from the reference contract"
FIXTURE_ID="$(echo "$PAIR" | jq -r '.fixture_id')"
IMPORT_ARM="$(echo "$PAIR" | jq -c '.recording_import')"
STREAM_ARM="$(echo "$PAIR" | jq -c '.streaming_acquisition')"
RUN_A="$(echo "$IMPORT_ARM" | jq -r '.robot_run_id')"
RUN_B="$(echo "$STREAM_ARM" | jq -r '.robot_run_id')"
echo "$PAIR" | jq -c '{fixture_id, sha256: .recording.sha256, message_count: .recording.message_count, channels: (.recording.topic_counts | length)}'
echo "  Recording Import       $RUN_A"
echo "  Streaming Acquisition  $RUN_B"
echo ""

echo "=== [2/5] the platform before: every RobotRun, Dataset, Scene and Episode ==="
BEFORE="$("${CONTRACT_TOOL[@]}" fingerprint)"
echo "$BEFORE" | jq -c '.counts'
echo ""

echo "=== [3/5] the registered recordings and manifests of both RobotRuns ==="
IMPORT_REQUEST="$(arm_request "$IMPORT_ARM")"
STREAM_REQUEST="$(arm_request "$STREAM_ARM")"
REQUEST="$(jq -cn --argjson pair "$PAIR" --argjson a "$IMPORT_REQUEST" --argjson b "$STREAM_REQUEST" '{
  locked: $pair.recording,
  recording_import: $a,
  streaming_acquisition: $b}')"
echo "$REQUEST" | jq -c '[.recording_import, .streaming_acquisition][]
  | {robot_run_id, recording: .recording.uri, bytes: .recording.size_bytes,
     scenes: (.scene_manifest_uris | length), episodes: (.episode_manifest_uris | length)}'
echo ""

echo "=== [4/5] equivalence, read from the ArtifactStore (one-shot recording-publisher container) ==="
VERIFY="$(compose run --rm -T -v "$REPO_ROOT/scripts/e2e:/workspace/e2e:ro" \
  --entrypoint python recording-publisher "$VERIFIER" <<<"$REQUEST")" || {
  echo "$VERIFY" | jq '.failures' >&2 || true
  fail "the Recording Import and Streaming Acquisition RobotRuns of $FIXTURE_ID are not equivalent"
}
echo "$VERIFY" | jq -c '{equivalent, recordings: (.recordings | map_values({size_bytes, message_count, capture_source_kind})), recording_equivalence, tf_static_messages, scene, episode}'
check "identity: the import pins the locked recording from a file; the stream is a different recording captured from Kafka; both carry the locked counts" \
  [ "$(echo "$VERIFY" | jq -r --arg a "$RUN_A" --arg b "$RUN_B" --arg sha "$(echo "$PAIR" | jq -r '.recording.sha256')" \
      '[.recordings.recording_import.checksum == $sha, .recordings.recording_import.capture_source_kind == "file",
        .recordings.streaming_acquisition.checksum != $sha, .recordings.streaming_acquisition.capture_source_kind == "kafka",
        .recordings.recording_import.robot_run_id == $a, .recordings.streaming_acquisition.robot_run_id == $b] | all')" = true ]
check "acquisition: same channels, message types and encodings, per-channel payload sequences and counts" \
  [ "$(echo "$VERIFY" | jq -r '.recording_equivalence.equivalent')" = true ]
check "acquisition: same source observation times (every Header.stamp and the mission event times)" \
  [ "$(echo "$VERIFY" | jq -r '(.failures | map(select(startswith("source observation times") or startswith("mission event"))) | length) == 0 and (.source_stamp_channels | length) > 0')" = true ]
check "acquisition: /tf_static is preserved in both recordings" \
  [ "$(echo "$VERIFY" | jq -r '(.tf_static_messages | to_entries | map(.value >= 1) | all)')" = true ]
check "canonical: every Scene's and Episode's semantic content is equal across the two RobotRuns" \
  [ "$(echo "$VERIFY" | jq -r '.equivalent')" = true ]
echo "$VERIFY" | jq -c '.negative_controls'
check "negative control: every perturbation is detected (a dropped message, a 1 ns source-time shift, a changed payload checksum, a Scene and an Episode semantic change)" \
  [ "$(echo "$VERIFY" | jq -r '(.negative_controls | length) >= 6 and (.negative_controls | to_entries | map(.value) | all)')" = true ]
echo ""

echo "=== [5/5] the platform after: nothing was added, removed or rewritten ==="
AFTER="$("${CONTRACT_TOOL[@]}" fingerprint)"
echo "$AFTER" | jq -c '.counts'
check "every RobotRun, Dataset, Scene and Episode record is exactly as it was before the journey" \
  [ "$(echo "$AFTER" | jq -cS .)" = "$(echo "$BEFORE" | jq -cS .)" ]
echo ""
echo "=== streaming equivalence E2E complete (read-only): fixture=$FIXTURE_ID import=$RUN_A stream=$RUN_B ==="
