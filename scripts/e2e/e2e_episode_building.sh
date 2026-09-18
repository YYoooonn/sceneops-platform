#!/usr/bin/env bash
# e2e_episode_building.sh
#
# E2E test for the RAW_LOG_EPISODE_BUILDING pipeline (SceneOps V2 Request 14):
#   POST /pipelines/runs (type=raw_log_episode_building)
#     -> execute -> build_episodes -> register_episode -> EpisodeRecord
#
# Deliberately routes through the real pipeline API rather than manually
# dispatching build_episodes/register_episode as two standalone jobs and
# hand-carrying episode_manifest_uris between them (the pre-Request-14
# version of this script did that) — the generic
# PipelineInputResolver/PipelineTaskResultRecorder mechanism already
# propagates build_episodes' REF-kind `episode_manifest_uris` output into
# register_episode's params, the same way it does for every other pipeline
# (e.g. build_scenes -> register_scene), so no Episode-specific glue is
# needed here.
#
# Deliberately separate from e2e_robot_can_replay.sh (Phase 4's
# ingest_robot_states chain) — this exercises the *Episode* domain built on
# top of the same RosbagAdapter/Robot infrastructure, not Robot/Mission
# ingestion itself. Reuses the MCAP already recorded by
# e2e_robot_can_replay.sh (data/raw/rosbag/<scene>/<scene>_0.mcap) rather
# than re-running the ROS2 recording step, to keep this test focused.
#
# There is no dedicated /episodes API surface yet (out of scope for this
# pass) — verification goes through pipeline task results, the generic
# /artifacts API (owner_type=episode), and the dataset version's episode
# summary. build_episodes is the sole registrar of the EPISODE_MANIFEST
# ArtifactRecord (SceneOps V2 Request 15 removed register_episode's
# duplicate registration of the same manifest URI) — step 10 below asserts
# exactly one artifact per episode, not two.
#
# force:true on pipeline-run creation (not a new mechanism — the same
# CreatePipelineRunRequest.force already used by other E2E scripts) makes
# repeated runs against this persistent local stack deterministic: without
# it, a second run with the same dataset_id/dataset_version/params hits the
# execution-key dedup cache and gets back the already-succeeded run from a
# prior invocation instead of executing fresh.
#
# Usage:
#   bash scripts/e2e/e2e_episode_building.sh
#
# Prereq:
#   Run e2e_robot_can_replay.sh at least once first (or otherwise populate
#   data/raw/rosbag/<scene>/<scene>_0.mcap) so MCAP_URI exists.
#
# Env overrides:
#   API_BASE_URL              (default: http://localhost:8000)
#   SCENE                     nuScenes scene name whose bag to reuse (default: scene-0061)
#   ROBOT_ID                  (default: robot-nuscenes-01)
#   DATASET_ID                (default: episodes-e2e)
#   DATASET_VERSION           (default: v1)
#   SEGMENTATION_STRATEGY     mission_boundary | whole_run | fixed_window (default: mission_boundary)
#   FIXED_WINDOW_DURATION_MS  only used when SEGMENTATION_STRATEGY=fixed_window (default: 5000)
#   POLL_TIMEOUT              max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SCENE="${SCENE:-scene-0061}"
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-01}"
DATASET_ID="${DATASET_ID:-episodes-e2e}"
DATASET_VERSION="${DATASET_VERSION:-v1}"
SEGMENTATION_STRATEGY="${SEGMENTATION_STRATEGY:-mission_boundary}"
FIXED_WINDOW_DURATION_MS="${FIXED_WINDOW_DURATION_MS:-5000}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

RUN_ID="run-${SCENE}-episodes"
BAG_DIR="/data/raw/rosbag/${SCENE}"
MCAP_URI="${BAG_DIR}/${SCENE}_0.mcap"

echo "=== raw_log_episode_building pipeline E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  SCENE=$SCENE  ROBOT_ID=$ROBOT_ID  RUN_ID=$RUN_ID"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  SEGMENTATION_STRATEGY=$SEGMENTATION_STRATEGY"
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

# ── 2. Build the segmentation config block ───────────────────────────────────

if [ "$SEGMENTATION_STRATEGY" = "fixed_window" ]; then
  SEGMENTATION_JSON="{\"strategy\": \"fixed_window\", \"fixed_window_duration_ms\": $FIXED_WINDOW_DURATION_MS}"
else
  SEGMENTATION_JSON="{\"strategy\": \"$SEGMENTATION_STRATEGY\"}"
fi

# ── 3. Create + execute the pipeline run ─────────────────────────────────────

echo "--- 2. Create raw_log_episode_building pipeline run ---"
PAYLOAD="$(cat <<JSON
{
  "type": "raw_log_episode_building",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "build_episodes": {
      "robot_id": "$ROBOT_ID",
      "robot_run_id": "$RUN_ID",
      "segmentation": $SEGMENTATION_JSON
    },
    "register_episode": {
      "replace_existing": true
    }
  }
}
JSON
)"

CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$PAYLOAD")"
PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESP")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo ""

echo "--- 3. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 4. Poll for completion ────────────────────────────────────────────────────

echo "--- 4. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 5. Assert pipeline succeeded ─────────────────────────────────────────────

echo "--- 5. Assert pipeline ---"
FINAL_STATUS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$FINAL_STATUS"
if [ "$FINAL_STATUS" != "succeeded" ]; then
  echo "  error=$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.error.message // "unknown"')"
fi
assert_pipeline_succeeded "$PIPELINE_JSON" 'raw_log_episode_building pipeline should succeed'
echo "  OK"
echo ""

# ── 6. Assert both tasks succeeded ───────────────────────────────────────────

echo "--- 6. Assert tasks ---"
TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
FAILED_TASKS="$(echo "$TASKS_JSON" | jq -r '[.tasks[] | select(.status != "succeeded")] | map("\(.pipelineTaskId)=\(.status)") | join(", ")')"
echo "$TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'
if [ -n "$FAILED_TASKS" ]; then
  echo "❌ Non-succeeded tasks: $FAILED_TASKS" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 7. Assert build_episodes outputs (no manual URI passing needed) ─────────

echo "--- 7. Assert build_episodes outputs ---"
BUILD_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "build_episodes")')"

EPISODE_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.summary.episode_count // 0')"
SEGMENTATION_STRATEGY_OUT="$(echo "$BUILD_TASK" | jq -r '.result.summary.segmentation_strategy // empty')"
EPISODE_MANIFEST_URIS_COUNT="$(echo "$BUILD_TASK" | jq -r '.result.refs.episode_manifest_uris | length // 0')"

echo "  episode_count=$EPISODE_COUNT"
echo "  segmentation_strategy=$SEGMENTATION_STRATEGY_OUT"
echo "  episode_manifest_uris count=$EPISODE_MANIFEST_URIS_COUNT"

assert_json_gt "$BUILD_TASK" '.result.summary.episode_count' 0 'expected episode_count > 0'
assert_json_equals "$BUILD_TASK" '.result.summary.segmentation_strategy' \
  "$SEGMENTATION_STRATEGY" 'segmentation_strategy should echo the requested strategy'
[ "${EPISODE_MANIFEST_URIS_COUNT:-0}" -ge 1 ] || {
  echo "❌ build_episodes: expected episode_manifest_uris ref" >&2
  exit 1
}
echo "  OK"
echo ""

# ── 8. Assert register_episode outputs ───────────────────────────────────────

echo "--- 8. Assert register_episode outputs ---"
REGISTER_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "register_episode")')"
REGISTERED_COUNT="$(echo "$REGISTER_TASK" | jq -r '.result.summary.registered_episode_count // 0')"
echo "  registered_episode_count=$REGISTERED_COUNT"
assert_json_gt "$REGISTER_TASK" '.result.summary.registered_episode_count' 0 \
  'expected registered_episode_count > 0'
[ "$REGISTERED_COUNT" = "$EPISODE_COUNT" ] || {
  echo "❌ registered_episode_count ($REGISTERED_COUNT) != episode_count ($EPISODE_COUNT)" >&2
  exit 1
}
echo "  OK"
echo ""

# ── 9. Verify DatasetVersion.episode.episodeCount matches what was persisted ─

echo "--- 9. Verify DatasetVersion episode summary ---"
VERSION_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")")"
VERSION_EPISODE_COUNT="$(echo "$VERSION_JSON" | jq -r '.version.episode.episodeCount // 0')"
echo "  dataset_version.episode.episodeCount=$VERSION_EPISODE_COUNT"
# Compared against registered_episode_count (register_episode's own count of
# rows it actually upserted into EpisodeStore), not build_episodes' count —
# that's the closer match for "actual persisted EpisodeRecord count".
[ "$VERSION_EPISODE_COUNT" = "$REGISTERED_COUNT" ] || {
  echo "❌ DatasetVersion.episode.episodeCount ($VERSION_EPISODE_COUNT) != registered_episode_count ($REGISTERED_COUNT)" >&2
  exit 1
}
echo "  OK"
echo ""

# ── 10. Verify persisted EPISODE artifacts via the generic artifacts API ─────
#
# Exactly one ArtifactRecord per episode manifest — build_episodes is the
# sole registrar (SceneOps V2 Request 15 removed register_episode's
# duplicate registration of the same manifest URI).

echo "--- 10. Verify persisted episode artifacts (exactly one per episode) ---"
ARTIFACTS_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/artifacts?owner_type=episode&pipeline_run_id=$PIPELINE_RUN_ID")")"
ARTIFACT_COUNT="$(echo "$ARTIFACTS_JSON" | jq -r '.count // 0')"
echo "  episode artifacts registered under this pipeline_run_id=$ARTIFACT_COUNT"
[ "$ARTIFACT_COUNT" = "$EPISODE_COUNT" ] || {
  echo "❌ Expected exactly $EPISODE_COUNT episode artifacts (one per episode), got $ARTIFACT_COUNT" >&2
  exit 1
}
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  robot_id=$ROBOT_ID  run_id=$RUN_ID"
echo "  segmentation_strategy=$SEGMENTATION_STRATEGY_OUT"
echo "  episode_count=$EPISODE_COUNT  registered_episode_count=$REGISTERED_COUNT"
echo "  dataset_version.episode.episodeCount=$VERSION_EPISODE_COUNT"
