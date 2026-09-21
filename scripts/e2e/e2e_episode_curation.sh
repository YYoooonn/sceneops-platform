#!/usr/bin/env bash
# e2e_episode_curation.sh
#
# E2E test for CURATE_EPISODES (SceneOps V2 Request 2.6):
#   ALIGN_EPISODE (x2, two TemporalAlignmentConfig revisions of the SAME
#   episode) -> PROFILE_ALIGNED_EPISODE/VALIDATE_ALIGNED_EPISODE (x2)
#     -> EXPORT_LEARNING_DATA (both revisions pinned into one snapshot)
#     -> CURATE_EPISODES (policy tuned, from the real observed profile
#        metrics, so exactly one revision is selected and the other
#        rejected)
#
# Deliberately curates two revisions of ONE episode_id rather than two
# different episodes — this is the strongest demonstration of Request 2.6
# §13/§18's "selection is over aligned revisions, not merely episode_id"
# requirement: same episode_id, two different aligned_artifact_checksums,
# independently selected/rejected.
#
# The curation policy's max_overall_missing_ratio threshold is computed at
# runtime as the midpoint between the two real PROFILE_ALIGNED_EPISODE
# results, not hardcoded — this script makes no assumption about the exact
# metric values the real fixture produces, only that two different
# target_frequency_hz configs produce two different overall_missing_ratio
# values (true for any real, non-uniformly-sampled MCAP fixture).
#
# Usage:
#   bash scripts/e2e/e2e_episode_curation.sh
#
# Prereq:
#   Run e2e_episode_building.sh at least once first (or otherwise have a
#   registered EPISODE_MANIFEST for EPISODE_ID in DATASET_ID/DATASET_VERSION).
#
# Env overrides:
#   API_BASE_URL     (default: http://localhost:8000)
#   DATASET_ID       (default: episodes-e2e)
#   DATASET_VERSION  (default: v1)
#   EPISODE_ID       (default: episodes-e2e-v1-episodes-mission-scene-0061)
#   POLL_TIMEOUT     max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
DATASET_ID="${DATASET_ID:-episodes-e2e}"
DATASET_VERSION="${DATASET_VERSION:-v1}"
EPISODE_ID="${EPISODE_ID:-episodes-e2e-v1-episodes-mission-scene-0061}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

echo "=== CURATE_EPISODES E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  EPISODE_ID=$EPISODE_ID"
echo ""

# ── 0. Verify the episode exists ─────────────────────────────────────────────

echo "--- 0. Verify episode exists ---"
EPISODE_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/episodes/$EPISODE_ID")")"
echo "$EPISODE_JSON" | jq -e '.episode.episodeId' >/dev/null || {
  echo "❌ Episode not found: $EPISODE_ID -- run e2e_episode_building.sh first" >&2
  exit 1
}
echo "  OK"
echo ""

# ── 1. ALIGN_EPISODE x2 (two distinct TemporalAlignmentConfig revisions) ────

align_episode_job() {
  local target_hz="$1"
  local payload
  payload="$(cat <<JSON
{
  "type": "align_episode",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "episode_id": "$EPISODE_ID",
    "alignment_config": {
      "target_frequency_hz": $target_hz,
      "tolerance_us": 200000,
      "max_gap_us": 2000000
    },
    "source_context": {
      "source_clock": "mcap_log_time"
    }
  }
}
JSON
)"
  local create_resp job_id job_json
  create_resp="$(create_job "$API_BASE_URL" "$payload")"
  job_id="$(extract_job_id "$create_resp")"
  execute_job "$API_BASE_URL" "$job_id" >/dev/null
  job_json="$(poll_job_terminal "$API_BASE_URL" "$job_id" "$POLL_TIMEOUT" 3)"
  assert_job_succeeded "$job_json" "align_episode (target_frequency_hz=$target_hz) should succeed"
  echo "$job_json"
}

echo "--- 1. ALIGN_EPISODE (target_frequency_hz=5.0) ---"
ALIGN_A_JSON="$(align_episode_job 5.0)"
ALIGN_A_ARTIFACT_ID="$(echo "$ALIGN_A_JSON" | jq -r '.job.result.aligned_artifact_id')"
ALIGN_A_CHECKSUM="$(echo "$ALIGN_A_JSON" | jq -r '.job.result.aligned_artifact_checksum')"
echo "  aligned_artifact_id=$ALIGN_A_ARTIFACT_ID checksum=${ALIGN_A_CHECKSUM:0:16}..."
echo ""

echo "--- 1b. ALIGN_EPISODE (target_frequency_hz=0.2) ---"
ALIGN_B_JSON="$(align_episode_job 0.2)"
ALIGN_B_ARTIFACT_ID="$(echo "$ALIGN_B_JSON" | jq -r '.job.result.aligned_artifact_id')"
ALIGN_B_CHECKSUM="$(echo "$ALIGN_B_JSON" | jq -r '.job.result.aligned_artifact_checksum')"
echo "  aligned_artifact_id=$ALIGN_B_ARTIFACT_ID checksum=${ALIGN_B_CHECKSUM:0:16}..."
echo ""

[ "$ALIGN_A_CHECKSUM" != "$ALIGN_B_CHECKSUM" ] || {
  echo "❌ Expected two distinct aligned_artifact_checksums for the two configs" >&2
  exit 1
}

# ── 2. PROFILE_ALIGNED_EPISODE x2 + VALIDATE_ALIGNED_EPISODE x2 ─────────────

profile_job() {
  local artifact_id="$1"
  local payload
  payload="$(cat <<JSON
{
  "type": "profile_aligned_episode",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "episode_id": "$EPISODE_ID",
    "aligned_artifact_id": "$artifact_id"
  }
}
JSON
)"
  local create_resp job_id job_json
  create_resp="$(create_job "$API_BASE_URL" "$payload")"
  job_id="$(extract_job_id "$create_resp")"
  execute_job "$API_BASE_URL" "$job_id" >/dev/null
  job_json="$(poll_job_terminal "$API_BASE_URL" "$job_id" "$POLL_TIMEOUT" 3)"
  assert_job_succeeded "$job_json" "profile_aligned_episode should succeed"
  echo "$job_json"
}

validate_job() {
  local artifact_id="$1"
  local payload
  payload="$(cat <<JSON
{
  "type": "validate_aligned_episode",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "episode_id": "$EPISODE_ID",
    "aligned_artifact_id": "$artifact_id"
  }
}
JSON
)"
  local create_resp job_id job_json
  create_resp="$(create_job "$API_BASE_URL" "$payload")"
  job_id="$(extract_job_id "$create_resp")"
  execute_job "$API_BASE_URL" "$job_id" >/dev/null
  job_json="$(poll_job_terminal "$API_BASE_URL" "$job_id" "$POLL_TIMEOUT" 3)"
  assert_job_succeeded "$job_json" "validate_aligned_episode should succeed"
  echo "$job_json"
}

echo "--- 2. PROFILE_ALIGNED_EPISODE + VALIDATE_ALIGNED_EPISODE (revision A) ---"
PROFILE_A_JSON="$(profile_job "$ALIGN_A_ARTIFACT_ID")"
VALIDATE_A_JSON="$(validate_job "$ALIGN_A_ARTIFACT_ID")"
RATIO_A="$(echo "$PROFILE_A_JSON" | jq -r '.job.result.overall_missing_ratio // 0')"
VALID_A="$(echo "$VALIDATE_A_JSON" | jq -r '.job.result.valid')"
echo "  overall_missing_ratio=$RATIO_A  valid=$VALID_A"
echo ""

echo "--- 2b. PROFILE_ALIGNED_EPISODE + VALIDATE_ALIGNED_EPISODE (revision B) ---"
PROFILE_B_JSON="$(profile_job "$ALIGN_B_ARTIFACT_ID")"
VALIDATE_B_JSON="$(validate_job "$ALIGN_B_ARTIFACT_ID")"
RATIO_B="$(echo "$PROFILE_B_JSON" | jq -r '.job.result.overall_missing_ratio // 0')"
VALID_B="$(echo "$VALIDATE_B_JSON" | jq -r '.job.result.valid')"
echo "  overall_missing_ratio=$RATIO_B  valid=$VALID_B"
echo ""

[ "$VALID_A" = "true" ] && [ "$VALID_B" = "true" ] || {
  echo "❌ Expected both aligned revisions to be structurally valid" >&2
  exit 1
}

# ── 3. EXPORT_LEARNING_DATA (both revisions pinned) ─────────────────────────

echo "--- 3. EXPORT_LEARNING_DATA (both revisions) ---"
EXPORT_PAYLOAD="$(cat <<JSON
{
  "type": "export_learning_data",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "inputs": [
      {"episode_id": "$EPISODE_ID", "aligned_artifact_id": "$ALIGN_A_ARTIFACT_ID"},
      {"episode_id": "$EPISODE_ID", "aligned_artifact_id": "$ALIGN_B_ARTIFACT_ID"}
    ]
  }
}
JSON
)"
EXPORT_CREATE_RESP="$(create_job "$API_BASE_URL" "$EXPORT_PAYLOAD")"
EXPORT_JOB_ID="$(extract_job_id "$EXPORT_CREATE_RESP")"
execute_job "$API_BASE_URL" "$EXPORT_JOB_ID" >/dev/null
EXPORT_JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$EXPORT_JOB_ID" "$POLL_TIMEOUT" 3)"
assert_job_succeeded "$EXPORT_JOB_JSON" "export_learning_data should succeed"

EXPORT_MANIFEST_ARTIFACT_ID="$(echo "$EXPORT_JOB_JSON" | jq -r '.job.result.manifest_artifact_id')"
EXPORT_EPISODE_COUNT="$(echo "$EXPORT_JOB_JSON" | jq -r '.job.result.episode_count')"
echo "  manifest_artifact_id=$EXPORT_MANIFEST_ARTIFACT_ID  episode_count=$EXPORT_EPISODE_COUNT"
[ -n "$EXPORT_MANIFEST_ARTIFACT_ID" ] && [ "$EXPORT_MANIFEST_ARTIFACT_ID" != "null" ] || {
  echo "❌ Expected a manifest_artifact_id from export_learning_data" >&2
  exit 1
}
echo "  OK"
echo ""

# ── 4. CURATE_EPISODES with a data-driven threshold ─────────────────────────
#
# Threshold = midpoint between the two revisions' real overall_missing_ratio
# -- guarantees exactly one revision selected, one rejected, without
# hardcoding an assumed value for this fixture.

echo "--- 4. Compute data-driven curation policy threshold ---"
THRESHOLD="$(python3 -c "print((float('$RATIO_A') + float('$RATIO_B')) / 2)")"
echo "  overall_missing_ratio: A=$RATIO_A  B=$RATIO_B  threshold=$THRESHOLD"
if [ "$RATIO_A" = "$RATIO_B" ]; then
  echo "❌ Both revisions have identical overall_missing_ratio -- cannot force a" >&2
  echo "   selected/rejected split via this metric. Adjust the two" >&2
  echo "   target_frequency_hz values in this script and re-run." >&2
  exit 1
fi
echo ""

echo "--- 4b. CURATE_EPISODES ---"
CURATE_PAYLOAD="$(cat <<JSON
{
  "type": "curate_episodes",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "learning_data_export_manifest_artifact_id": "$EXPORT_MANIFEST_ARTIFACT_ID",
    "policy": {
      "max_overall_missing_ratio": $THRESHOLD
    }
  }
}
JSON
)"
CURATE_CREATE_RESP="$(create_job "$API_BASE_URL" "$CURATE_PAYLOAD")"
CURATE_JOB_ID="$(extract_job_id "$CURATE_CREATE_RESP")"
execute_job "$API_BASE_URL" "$CURATE_JOB_ID" >/dev/null
CURATE_JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$CURATE_JOB_ID" "$POLL_TIMEOUT" 3)"
assert_job_succeeded "$CURATE_JOB_JSON" "curate_episodes should succeed"

CANDIDATE_COUNT="$(echo "$CURATE_JOB_JSON" | jq -r '.job.result.candidate_count')"
SELECTED_COUNT="$(echo "$CURATE_JOB_JSON" | jq -r '.job.result.selected_count')"
REJECTED_COUNT="$(echo "$CURATE_JOB_JSON" | jq -r '.job.result.rejected_count')"
CURATION_ID="$(echo "$CURATE_JOB_JSON" | jq -r '.job.result.curation_id')"
MANIFEST_ARTIFACT_ID="$(echo "$CURATE_JOB_JSON" | jq -r '.job.result.manifest_artifact_id')"
MANIFEST_URI="$(echo "$CURATE_JOB_JSON" | jq -r '.job.result.manifest_uri')"

echo "  candidate_count=$CANDIDATE_COUNT  selected_count=$SELECTED_COUNT  rejected_count=$REJECTED_COUNT"
echo "  curation_id=$CURATION_ID"
echo "  manifest_artifact_id=$MANIFEST_ARTIFACT_ID"
echo "  manifest_uri=$MANIFEST_URI"

[ "$CANDIDATE_COUNT" = "2" ] || {
  echo "❌ Expected candidate_count=2 (both revisions of $EPISODE_ID), got $CANDIDATE_COUNT" >&2
  exit 1
}
[ "$SELECTED_COUNT" = "1" ] && [ "$REJECTED_COUNT" = "1" ] || {
  echo "❌ Expected exactly 1 selected + 1 rejected, got selected=$SELECTED_COUNT rejected=$REJECTED_COUNT" >&2
  exit 1
}
echo "  OK"
echo ""

# ── 5. Verify the persisted EpisodeCurationManifest via the real ArtifactStore ──

echo "--- 5. Verify persisted ArtifactRecord (real Postgres) ---"
ARTIFACTS_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/artifacts?owner_type=dataset_version&owner_id=$DATASET_ID:$DATASET_VERSION")")"
CURATION_ARTIFACT="$(echo "$ARTIFACTS_JSON" | jq --arg id "$MANIFEST_ARTIFACT_ID" '.artifacts[] | select(.artifactId == $id)')"
echo "$CURATION_ARTIFACT" | jq '{artifactId, kind, ownerType, ownerId, checksum}'
KIND="$(echo "$CURATION_ARTIFACT" | jq -r '.kind')"
[ "$KIND" = "episode_curation_manifest" ] || {
  echo "❌ Expected ArtifactRecord.kind=episode_curation_manifest, got $KIND" >&2
  exit 1
}
echo "  OK"
echo ""

echo "--- 6. Verify persisted manifest content (real MinIO) via decisions ---"
# No dedicated GET endpoint exposes EpisodeCurationManifest content (no
# LearningDataset/curation resource API exists per Request 2.6 §12/§17) --
# fetch the bytes directly from MinIO (the same s3://sceneops bucket the
# worker itself just wrote through ArtifactStore) via boto3, to prove the
# persisted reason codes/decisions match what the job result summarized,
# not just that *some* bytes exist at the URI.
MANIFEST_KEY="${MANIFEST_URI#s3://sceneops/}"
uv run python3 - "$MANIFEST_KEY" "$CANDIDATE_COUNT" "$SELECTED_COUNT" "$REJECTED_COUNT" <<'PYEOF'
import json
import sys

import boto3

key, expected_total, expected_selected, expected_rejected = sys.argv[1:5]
s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:9000",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="minioadmin",
)
body = s3.get_object(Bucket="sceneops", Key=key)["Body"].read()
manifest = json.loads(body)

decisions = manifest["decisions"]
assert len(decisions) == int(expected_total), (len(decisions), expected_total)
selected = [d for d in decisions if d["selected"]]
rejected = [d for d in decisions if not d["selected"]]
assert len(selected) == int(expected_selected), (len(selected), expected_selected)
assert len(rejected) == int(expected_rejected), (len(rejected), expected_rejected)
assert all(d["reasons"] == [] for d in selected), "selected candidates must have no reasons"
assert all(len(d["reasons"]) > 0 for d in rejected), "rejected candidates must have reasons"
for d in rejected:
    codes = [r["code"] for r in d["reasons"]]
    assert "max_overall_missing_ratio_exceeded" in codes, codes

print(f"  manifest bytes verified: {len(body)} bytes at s3://sceneops/{key}")
for d in decisions:
    reason_codes = [r["code"] for r in d["reasons"]]
    print(
        f"    episode_id={d['episodeId']} "
        f"aligned_artifact_checksum={d['alignedArtifactChecksum'][:16]}... "
        f"selected={d['selected']} reasons={reason_codes}"
    )
print("  OK")
PYEOF
echo ""

echo "=== PASSED ==="
echo "  episode_id=$EPISODE_ID"
echo "  revision A: aligned_artifact_id=$ALIGN_A_ARTIFACT_ID overall_missing_ratio=$RATIO_A"
echo "  revision B: aligned_artifact_id=$ALIGN_B_ARTIFACT_ID overall_missing_ratio=$RATIO_B"
echo "  export_manifest_artifact_id=$EXPORT_MANIFEST_ARTIFACT_ID"
echo "  curation_id=$CURATION_ID"
echo "  selected=$SELECTED_COUNT rejected=$REJECTED_COUNT of candidate_count=$CANDIDATE_COUNT"
