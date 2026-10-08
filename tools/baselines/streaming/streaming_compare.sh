#!/usr/bin/env bash
# streaming_compare.sh — read-only corpus-level comparison of the batch reference
# baseline (ref-<selection>) and the streaming reference baseline
# (stream-ref-<selection>) built from the same locked corpus.
#
#   same fixture set                          10 vs 10 RobotRuns, Scenes, Episodes
#   source-level facts, per fixture           the locked recording's message count and
#                                             per-channel counts == the batch RobotRun's
#                                             registered manifest == the streamed one's
#   canonical semantic compatibility          every Scene and Episode of a fixture has an
#                                             equal semantic projection (I-35) on both sides
#
# It loads manifests only, never a recording payload: the payload-level proof of
# the transport is `make e2e-streaming-equivalence`, on one fixture. Creates and
# mutates nothing. Uses only FastAPI and one-shot containers.
#
# Selection as the baselines (REFERENCE_SCOPE, FIXTURE); BATCH_BASELINE_ID /
# STREAM_BASELINE_ID override the default identities. Prints one JSON report on
# stdout; exits non-zero on the first violated expectation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/tools/e2e/lib.sh"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
export API_BASE_URL ENV_FILE

# Both baselines are verified by their own read-only commands, each with its own identity.
BATCH_ID="${BATCH_BASELINE_ID:-}"
STREAM_ID="${STREAM_BASELINE_ID:-}"
BATCH_SUMMARY="$(BASELINE_ID="$BATCH_ID" "$REPO_ROOT/tools/baselines/canonical/canonical_verify.sh")"
STREAM_SUMMARY="$(BASELINE_ID="$STREAM_ID" "$SCRIPT_DIR/streaming_verify.sh")"
BATCH_ID="$(echo "$BATCH_SUMMARY" | jq -r '.baseline_id')"
STREAM_ID="$(echo "$STREAM_SUMMARY" | jq -r '.baseline_id')"
[ "$BATCH_ID" != "$STREAM_ID" ] || fail "the batch and the streaming baseline are one baseline ($BATCH_ID)"

log() { echo "$@" >&2; }
log "=== batch $BATCH_ID  vs  streaming $STREAM_ID ==="

# --- corpus level
CORPUS="$(jq -cn --argjson b "$BATCH_SUMMARY" --argjson s "$STREAM_SUMMARY" '{
  same_fixture_set: (([$b.fixtures[].fixture_id] | sort) == ([$s.fixtures[].fixture_id] | sort)),
  totals: {batch: $b.totals, stream: $s.totals},
  same_counts: ([$b.totals.robot_runs, $b.totals.scenes, $b.totals.episodes, $b.totals.messages]
                == [$s.totals.robot_runs, $s.totals.scenes, $s.totals.episodes, $s.totals.messages]),
  same_source_recording: ([$b.fixtures[] | [.fixture_id, .recording_sha256]] == [$s.fixtures[] | [.fixture_id, .recording_sha256]])}')"
echo "$CORPUS" | jq -e '.same_fixture_set and .same_counts and .same_source_recording' >/dev/null \
  || { echo "$CORPUS" | jq . >&2; fail "the baselines differ at corpus level"; }
log "  ✅  same fixtures; $(echo "$CORPUS" | jq -c '.totals.stream | {robot_runs, scenes, episodes, messages}') on both sides; same locked recordings"

# --- per-fixture source facts and manifest URIs (batch and streamed RobotRuns)
manifest_uri() { # <artifact-id>
  api_get "$API_BASE_URL" "/artifacts/$1" | jq -r '.artifact.uri'
}
unit_uris() { # <scenes|episodes> <dataset-id> <version> <run-id> -> JSON array of manifest URIs
  local ids id
  ids="$(api_get "$API_BASE_URL" "/$1?dataset_id=$2&dataset_version=$3&limit=500" \
    | jq -r --arg r "$4" "[.$1[] | select(.robotRunId == \$r) | .manifestArtifactId] | sort | .[]")"
  for id in $ids; do manifest_uri "$id"; done | jq -R . | jq -sc .
}

manifests="{}"
request="{}"
fixture_ids=()
while IFS= read -r id; do fixture_ids+=("$id"); done < <(echo "$STREAM_SUMMARY" | jq -r '.fixtures[].fixture_id')
for id in "${fixture_ids[@]}"; do
  batch_run="run-$BATCH_ID-$id"
  stream_run="run-$STREAM_ID-$id"
  for run in "$batch_run" "$stream_run"; do
    mid="$(api_get "$API_BASE_URL" "/robot-runs/$run" | jq -r '.robotRun.manifestArtifactId')"
    manifests="$(echo "$manifests" | jq -c --arg r "$run" --arg u "$(manifest_uri "$mid")" '. + {($r): $u}')"
  done
  request="$(echo "$request" | jq -c --arg id "$id" \
    --argjson bs "$(unit_uris scenes "$(echo "$BATCH_SUMMARY" | jq -r .dataset_id)" "$(echo "$BATCH_SUMMARY" | jq -r .dataset_version)" "$batch_run")" \
    --argjson be "$(unit_uris episodes "$(echo "$BATCH_SUMMARY" | jq -r .dataset_id)" "$(echo "$BATCH_SUMMARY" | jq -r .dataset_version)" "$batch_run")" \
    --argjson ss "$(unit_uris scenes "$(echo "$STREAM_SUMMARY" | jq -r .dataset_id)" "$(echo "$STREAM_SUMMARY" | jq -r .dataset_version)" "$stream_run")" \
    --argjson se "$(unit_uris episodes "$(echo "$STREAM_SUMMARY" | jq -r .dataset_id)" "$(echo "$STREAM_SUMMARY" | jq -r .dataset_version)" "$stream_run")" \
    '.fixtures[$id] = {batch: {scene_manifest_uris: $bs, episode_manifest_uris: $be},
                       stream: {scene_manifest_uris: $ss, episode_manifest_uris: $se}}')"
done
request="$(echo "$request" | jq -c '{fixtures: .fixtures}')"

MOUNTS=(-v "$REPO_ROOT/tools/baselines/streaming:/workspace/streaming:ro" -v "$REPO_ROOT/tools/e2e:/workspace/e2e:ro")
FACTS="$(compose run --rm -T "${MOUNTS[@]}" --entrypoint python recording-publisher \
  /workspace/streaming/manifest_facts.py <<<"$(jq -cn --argjson m "$manifests" '{manifests: $m}')")" \
  || fail "could not read the RobotRun manifests"

# Source-level facts: the locked recording, the batch RobotRun's manifest and the streamed one's
# agree on the message count and on every channel's count.
SOURCE="$(jq -cn --argjson b "$BATCH_SUMMARY" --argjson s "$STREAM_SUMMARY" --argjson f "$FACTS" \
  --arg bid "$BATCH_ID" --arg sid "$STREAM_ID" '
  [$s.fixtures[] | . as $x
   | ($f["run-" + $bid + "-" + $x.fixture_id]) as $bf | ($f["run-" + $sid + "-" + $x.fixture_id]) as $sf
   | {fixture_id: $x.fixture_id, locked_messages: $x.message_count,
      batch_messages: $bf.message_count, stream_messages: $sf.message_count,
      channels: ($sf.channel_counts | length),
      same_channel_counts: ($bf.channel_counts == $sf.channel_counts),
      batch_pins_locked: ($bf.recording.checksum == $x.recording_sha256),
      stream_is_a_capture: ($sf.recording.checksum != $x.recording_sha256)}]')"
echo "$SOURCE" | jq -e 'all(.[]; .locked_messages == .batch_messages and .batch_messages == .stream_messages
                           and .same_channel_counts and .batch_pins_locked and .stream_is_a_capture)' >/dev/null \
  || { echo "$SOURCE" | jq . >&2; fail "source-level message / channel facts differ between the baselines"; }
log "  ✅  per fixture: locked == batch == streamed message count and per-channel counts; the batch run pins the locked recording, the streamed one its own capture"

# Canonical semantic compatibility of every Scene and Episode.
SEMANTIC="$(compose run --rm -T "${MOUNTS[@]}" --entrypoint python recording-publisher \
  /workspace/streaming/baseline_compare.py <<<"$request")" \
  || fail "the batch and streaming baselines are not canonically compatible"
log "  ✅  every Scene and Episode of every fixture has an equal semantic projection in both baselines"

jq -cn --arg b "$BATCH_ID" --arg s "$STREAM_ID" --argjson corpus "$CORPUS" --argjson source "$SOURCE" \
  --argjson semantic "$SEMANTIC" '{
    batch_baseline_id: $b, stream_baseline_id: $s, compatible: $semantic.equivalent,
    corpus: $corpus, source_facts: $source, semantic: $semantic.fixtures}'
