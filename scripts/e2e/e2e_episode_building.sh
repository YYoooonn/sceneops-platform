#!/usr/bin/env bash
# e2e_episode_building.sh
#
# E2E test for the Episode domain (SceneOps V2 Phase 1):
#   Robot rosbag/MCAP -> build_episodes job (RosbagAdapter -> EpisodeBuilder,
#   segmented by Mission boundaries) -> register_episode job -> EpisodeRecord.
#
# Deliberately separate from e2e_robot_can_replay.sh (Phase 4's
# ingest_robot_states chain) — this exercises the *Episode* domain built on
# top of the same RosbagAdapter/Robot infrastructure, not Robot/Mission
# ingestion itself. Reuses the MCAP already recorded by
# e2e_robot_can_replay.sh (data/raw/rosbag/<scene>/<scene>_0.mcap) rather
# than re-running the ROS2 recording step, to keep this test focused.
#
# There is no dedicated /episodes API surface yet (out of scope for this
# pass) — verification goes through the build_episodes / register_episode
# job results and the dataset version's episode_count.
#
# Usage:
#   bash scripts/e2e/e2e_episode_building.sh
#
# Prereq:
#   Run e2e_robot_can_replay.sh at least once first (or otherwise populate
#   data/raw/rosbag/<scene>/<scene>_0.mcap) so MCAP_URI exists.
#
# Env overrides:
#   API_BASE_URL     (default: http://localhost:8000)
#   SCENE            nuScenes scene name whose bag to reuse (default: scene-0061)
#   ROBOT_ID         (default: robot-nuscenes-01)
#   DATASET_ID       (default: episodes-e2e)
#   DATASET_VERSION  (default: v1)
#   POLL_TIMEOUT     max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SCENE="${SCENE:-scene-0061}"
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-01}"
DATASET_ID="${DATASET_ID:-episodes-e2e}"
DATASET_VERSION="${DATASET_VERSION:-v1}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

RUN_ID="run-${SCENE}-episodes"
BAG_DIR="/data/raw/rosbag/${SCENE}"
MCAP_URI="${BAG_DIR}/${SCENE}_0.mcap"

echo "=== episode_building (Phase 1) E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  SCENE=$SCENE  ROBOT_ID=$ROBOT_ID  RUN_ID=$RUN_ID"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo ""

if [ ! -f "${REPO_ROOT}${MCAP_URI}" ]; then
  echo "❌ Expected bag file not found: ${REPO_ROOT}${MCAP_URI}" >&2
  echo "   Run e2e_robot_can_replay.sh first (or set SCENE to an existing bag)." >&2
  exit 1
fi
echo "  bag=${MCAP_URI}  OK"
echo ""

# ── 1. Register Robot + RobotRun + Dataset/DatasetVersion via API ───────────

echo "--- 1. Register Robot + RobotRun + Dataset/DatasetVersion ---"
upsert_robot "$API_BASE_URL" "$ROBOT_ID" "nuscenes-can-replay" \
  | jq '.robot | {robotId, status}'
upsert_robot_run "$API_BASE_URL" "$RUN_ID" "$ROBOT_ID" "$MCAP_URI" \
  | jq '.robotRun | {runId, robotId, mcapUri}'
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Episode building E2E" \
  | jq '.dataset | {datasetId}'
# No raw_source_root_uri — that's Scene-owned (SceneOps V2 Request 04) and
# meaningless here; the episode source is RobotRun.mcap_uri (registered
# above), not a dataset-version-level raw source root.
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" \
  | jq '.version | {datasetId, version, status, scene, episode}'
echo ""

# ── 2. Create + dispatch build_episodes job ──────────────────────────────────

echo "--- 2. build_episodes ---"
BUILD_PAYLOAD="$(cat <<JSON
{
  "type": "build_episodes",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {
    "robot_id": "$ROBOT_ID",
    "robot_run_id": "$RUN_ID"
  }
}
JSON
)"

BUILD_CREATE_RESP="$(create_job "$API_BASE_URL" "$BUILD_PAYLOAD")"
BUILD_JOB_ID="$(extract_job_id "$BUILD_CREATE_RESP")"
echo "  job_id=$BUILD_JOB_ID"

execute_job "$API_BASE_URL" "$BUILD_JOB_ID" >/dev/null

BUILD_JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$BUILD_JOB_ID" "$POLL_TIMEOUT" 5)"
assert_job_succeeded "$BUILD_JOB_JSON" 'build_episodes job should succeed'

EPISODE_COUNT="$(echo "$BUILD_JOB_JSON" | jq -r '.job.result.episode_count // 0')"
echo "  episode_count=$EPISODE_COUNT"
assert_json_gt "$BUILD_JOB_JSON" '.job.result.episode_count' 0 'expected episode_count > 0'

EPISODE_MANIFEST_URIS="$(echo "$BUILD_JOB_JSON" | jq -c '.job.result.episode_manifest_uris')"
echo "  episode_manifest_uris=$EPISODE_MANIFEST_URIS"
echo "  OK"
echo ""

# ── 3. Create + dispatch register_episode job ────────────────────────────────

echo "--- 3. register_episode ---"
REGISTER_PAYLOAD="$(cat <<JSON
{
  "type": "register_episode",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "params": {
    "episode_manifest_uris": $EPISODE_MANIFEST_URIS,
    "replace_existing": true
  }
}
JSON
)"

REGISTER_CREATE_RESP="$(create_job "$API_BASE_URL" "$REGISTER_PAYLOAD")"
REGISTER_JOB_ID="$(extract_job_id "$REGISTER_CREATE_RESP")"
echo "  job_id=$REGISTER_JOB_ID"

execute_job "$API_BASE_URL" "$REGISTER_JOB_ID" >/dev/null

REGISTER_JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$REGISTER_JOB_ID" "$POLL_TIMEOUT" 5)"
assert_job_succeeded "$REGISTER_JOB_JSON" 'register_episode job should succeed'

REGISTERED_COUNT="$(echo "$REGISTER_JOB_JSON" | jq -r '.job.result.registered_episode_count // 0')"
echo "  registered_episode_count=$REGISTERED_COUNT"
assert_json_gt "$REGISTER_JOB_JSON" '.job.result.registered_episode_count' 0 \
  'expected registered_episode_count > 0'
echo "  OK"
echo ""

# ── 4. Verify DatasetVersion.episode_count via API ───────────────────────────

echo "--- 4. Verify dataset version episode_count ---"
VERSION_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")")"
VERSION_EPISODE_COUNT="$(echo "$VERSION_JSON" | jq -r '.version.episode.episodeCount // 0')"
echo "  dataset_version.episode_count=$VERSION_EPISODE_COUNT"
[ "${VERSION_EPISODE_COUNT:-0}" -ge 1 ] || {
  echo "❌ Expected DatasetVersion.episode_count >= 1, got $VERSION_EPISODE_COUNT" >&2
  exit 1
}
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  build_job_id=$BUILD_JOB_ID  register_job_id=$REGISTER_JOB_ID"
echo "  robot_id=$ROBOT_ID  run_id=$RUN_ID"
echo "  episode_count=$EPISODE_COUNT  registered_episode_count=$REGISTERED_COUNT"
echo "  dataset_version.episode_count=$VERSION_EPISODE_COUNT"
