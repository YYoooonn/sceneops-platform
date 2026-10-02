#!/usr/bin/env bash
# e2e_robot_run_learning.sh — real-data E2E: the Episode/learning bridge
# boundary, end to end, over a streaming-captured RobotRun:
#
#   real nuScenes CAN -> ROS2 -> Kafka -> durable MCAP capture
#     -> Recording Publisher (MCAP + RobotRunManifest, real MinIO)
#     -> REGISTER_ROBOT_RUN (ArtifactRecords + canonical RobotRun)
#     -> materialize (sceneops_worker.robots.materialization, execution-
#        scoped local temp file, checksum-verified against the RobotRun's
#        own ArtifactRecord) -> existing RosbagAdapter (unmodified,
#        storage-agnostic -- it is handed a local path either way)
#     -> existing raw_log_episode_building pipeline
#        (build_episodes -> register_episode -> validate/profile_episode)
#     -> existing align_episode / profile_aligned_episode /
#        validate_aligned_episode jobs
#     -> existing export_learning_data job (v2-sharded)
#     -> scripts/canonical/verify_learning_export.py --open-dataset
#        (REUSED, unmodified -- real SceneOpsDataset.open() + step reads)
#
# Every stage after "canonical RobotRun" reuses the existing Episode/
# Phase 5 learning-data pipeline verbatim -- nothing here is a
# streaming-specific Episode/alignment/validation/profile/export
# implementation. The only new code this E2E exercises is the
# materialization boundary itself (BuildEpisodesJobHandler now
# materializes an ArtifactStore-backed mcap_uri before handing a local
# path to RosbagAdapter, exactly as it already did for a local path).
#
# Uses a fresh, isolated dataset_id per invocation (the "test-e2e-*"
# prefix convention, scripts/e2e/lib.sh) -- never
# sceneops-canonical/v0.0, the frozen canonical baseline.
#
# Prerequisites (this script does not do either of these for you):
#   make local-up       # Postgres + MinIO + api + worker-pipeline/worker-jobs
#   make streaming-up   # local Kafka broker
#
# Usage:
#   make e2e-robot-run-learning
#   SCENE=scene-0061 RATE=10.0 scripts/e2e/e2e_robot_run_learning.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib.sh"
cd "$REPO_ROOT"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SCENE="${SCENE:-scene-0061}"
RATE="${RATE:-10.0}"
DURATION="${DURATION:-20}"
ALIGN_FREQUENCY_HZ="${ALIGN_FREQUENCY_HZ:-5.0}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

INVOCATION_ID="$(date +%s)-$$"
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-learning}"
ROBOT_RUN_ID="run-learning-${INVOCATION_ID}"
DATASET_ID="${DATASET_ID:-test-e2e-robot-learning-${INVOCATION_ID}}"
DATASET_VERSION="${DATASET_VERSION:-test-v1}"

COMPOSE="docker compose --env-file .env.local"
CAPTURED_ROOT="/data/tmp_robot_run_learning/kafka-captured"
HOST_CAPTURED_ROOT="$REPO_ROOT/data/tmp_robot_run_learning/kafka-captured"

rm -rf "$HOST_CAPTURED_ROOT"

echo "=== e2e-robot-run-learning ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  robot_id=$ROBOT_ID robot_run_id=$ROBOT_RUN_ID"
echo "  dataset_id=$DATASET_ID dataset_version=$DATASET_VERSION"
echo ""

# ── 1. real CAN replay -> ROS2 -> bridge -> Kafka ────────────────────────────

echo "--- 1. real CAN replay -> ROS2 -> bridge -> Kafka ---"
BRIDGE_LOG="$(mktemp)"
CAPTURE_LOG="$(mktemp)"
trap 'rm -f "$BRIDGE_LOG" "$CAPTURE_LOG"' EXIT

$COMPOSE --profile ros2 run --rm ros2 sh -c "
  set -e
  timeout $DURATION python3 /workspace/nodes/streaming_bridge_node.py \
    --robot-id $ROBOT_ID --robot-run-id $ROBOT_RUN_ID &
  BRIDGE_PID=\$!
  sleep 2
  python3 /workspace/nodes/can_replay_node.py --scene $SCENE --rate $RATE
  wait \$BRIDGE_PID || true
" > "$BRIDGE_LOG" 2>&1
tail -5 "$BRIDGE_LOG"

PUBLISHED_COUNT="$(grep -oE 'published=[0-9]+' "$BRIDGE_LOG" | tail -1 | cut -d= -f2 || true)"
FAILED_COUNT="$(grep -oE 'failed=[0-9]+' "$BRIDGE_LOG" | tail -1 | cut -d= -f2 || true)"
if [ -z "${PUBLISHED_COUNT:-}" ] || [ "${FAILED_COUNT:-0}" != "0" ]; then
  echo "❌ bridge stage failed (published=${PUBLISHED_COUNT:-<missing>} failed=${FAILED_COUNT:-0})" >&2
  exit 1
fi
echo "  bridge reported: published=$PUBLISHED_COUNT failed=${FAILED_COUNT:-0}"
echo ""

# ── 2. durable MCAP capture ───────────────────────────────────────────────────

echo "--- 2. durable MCAP capture (Kafka -> ros2/capture) ---"
$COMPOSE --profile ros2 run --rm ros2 python3 /workspace/capture/cli.py \
  --robot-id "$ROBOT_ID" --robot-run-id "$ROBOT_RUN_ID" \
  --output-root "$CAPTURED_ROOT" \
  --max-messages "$PUBLISHED_COUNT" \
  > "$CAPTURE_LOG" 2>&1
cat "$CAPTURE_LOG"

CAPTURE_FIRST_SEQ="$(grep -oE 'first_sequence=[0-9]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
CAPTURE_SHA256="$(grep -oE 'sha256=[0-9a-f]+' "$CAPTURE_LOG" | tail -1 | cut -d= -f2)"
if [ "$CAPTURE_FIRST_SEQ" != "0" ] || [ -z "$CAPTURE_SHA256" ]; then
  echo "❌ capture stage produced unexpected results" >&2
  exit 1
fi
MCAP_PATH="$CAPTURED_ROOT/$ROBOT_RUN_ID/${ROBOT_RUN_ID}_0.mcap"
EXPECTED_CHECKSUM="sha256:$CAPTURE_SHA256"
echo "  captured sha256=$CAPTURE_SHA256"
echo ""

# ── 3. publish + REGISTER_ROBOT_RUN (real MinIO + Postgres) ─────────────────

echo "--- 3. publish recording + register canonical RobotRun ---"
REGISTER_JOB_JSON="$(publish_and_register_robot_run "$REPO_ROOT" "$API_BASE_URL" \
  "$ROBOT_ID" "$ROBOT_RUN_ID" "$MCAP_PATH" kafka "" "sceneops.robot.telemetry.v1")"
echo "$REGISTER_JOB_JSON" | jq '.job.result | {run_id, created, manifest_checksum}'
REGISTERED_RECORDING_ID="$(echo "$REGISTER_JOB_JSON" | jq -r '.job.result.recording_artifact_id')"
REGISTERED_CHECKSUM="$(curl -sS "$(api_url "$API_BASE_URL" "/artifacts/$REGISTERED_RECORDING_ID")" | jq -r '.artifact.checksum // empty')"
if [ "$REGISTERED_CHECKSUM" != "$EXPECTED_CHECKSUM" ]; then
  echo "❌ registered recording checksum ($REGISTERED_CHECKSUM) != captured ($EXPECTED_CHECKSUM)" >&2
  exit 1
fi
echo ""

# ── 4. upsert Dataset/DatasetVersion (fresh, isolated -- never the frozen ───
#      canonical baseline)

echo "--- 4. upsert Dataset/DatasetVersion ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Robot learning bridge E2E" | jq '.dataset | {datasetId}'
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" \
  | jq '.version | {datasetId, version, status}'
echo ""

# ── 5. raw_log_episode_building pipeline: build_episodes -> register_episode ─
#      -> validate_episode / profile_episode. robot_run_id only, NO mcap_uri
#      override -- forces resolution through the RobotRun's recording
#      ArtifactRecord (the s3:// URI), exercising the materialization
#      boundary for real.

echo "--- 5. raw_log_episode_building pipeline ---"
# Segmentation strategy is whole_run, not mission_boundary: a
# Kafka-captured MCAP's /mission/status carries synthetic present-day
# replay-event time (Phase 6.3's frozen, documented mapping -- see
# docs/architecture/streaming-transport.md's MCAP-readiness table),
# while CAN-derived channels carry real ~2018 observation time -- the
# two never overlap, so mission_boundary segmentation (which requires
# robot-state timestamps inside the mission's own window) yields zero
# episodes here. This never surfaces for a direct "ros2 bag record"
# capture (e2e_robot_learning.sh), because that path stamps every
# channel with the recorder's own receipt time uniformly, never
# threading source_timestamp_ns into log_time at all. whole_run needs
# no mission/CAN timestamp alignment -- an existing, already-implemented
# strategy choice via job params, not a pipeline change.
BUILD_PAYLOAD="$(cat <<JSON
{
  "type": "raw_log_episode_building",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "build_episodes": {
      "robot_id": "$ROBOT_ID",
      "robot_run_id": "$ROBOT_RUN_ID",
      "segmentation": {"strategy": "whole_run"}
    },
    "register_episode": {"replace_existing": true},
    "profile_episode": {"triggered": true}
  }
}
JSON
)"
BUILD_CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$BUILD_PAYLOAD")"
BUILD_PIPELINE_RUN_ID="$(extract_pipeline_run_id "$BUILD_CREATE_RESP")"
dispatch_pipeline_run "$API_BASE_URL" "$BUILD_PIPELINE_RUN_ID" >/dev/null
BUILD_PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$BUILD_PIPELINE_RUN_ID" "$POLL_TIMEOUT" 3)"
assert_pipeline_succeeded "$BUILD_PIPELINE_JSON" "raw_log_episode_building should succeed" "$API_BASE_URL" "$BUILD_PIPELINE_RUN_ID"

BUILD_TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$BUILD_PIPELINE_RUN_ID")"
BUILD_TASK="$(echo "$BUILD_TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "build_episodes")')"
EPISODE_IDS_JSON="$(echo "$BUILD_TASK" | jq -c '.result.rawResult.episode_ids')"
EPISODE_COUNT="$(echo "$EPISODE_IDS_JSON" | jq 'length')"
echo "  episode_count=$EPISODE_COUNT episode_ids=$EPISODE_IDS_JSON"
[ "${EPISODE_COUNT:-0}" -ge 1 ] || {
  echo "❌ build_episodes produced 0 episodes" >&2
  exit 1
}
echo ""

# ── 6. verify the built Episode references the streaming-sourced RobotRun ───

echo "--- 6. verify Episode <-> RobotRun linkage ---"
EPISODES_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/episodes?robot_run_id=$ROBOT_RUN_ID")")"
LINKED_COUNT="$(echo "$EPISODES_JSON" | jq '.episodes | length')"
[ "$LINKED_COUNT" = "$EPISODE_COUNT" ] || {
  echo "❌ GET /episodes?robot_run_id=$ROBOT_RUN_ID returned $LINKED_COUNT, expected $EPISODE_COUNT" >&2
  exit 1
}
EPISODE_ID="$(echo "$EPISODE_IDS_JSON" | jq -r '.[0]')"
OBSERVATION_CHANNELS="$(echo "$EPISODES_JSON" | jq -r --arg e "$EPISODE_ID" \
  '.episodes[] | select(.episodeId == $e) | .observationChannels | join(",")')"
ACTION_CHANNELS="$(echo "$EPISODES_JSON" | jq -r --arg e "$EPISODE_ID" \
  '.episodes[] | select(.episodeId == $e) | .actionChannels | join(",")')"
echo "  episode_id=$EPISODE_ID observation_channels=$OBSERVATION_CHANNELS action_channels=$ACTION_CHANNELS"
echo ""

# ── 7. align / profile / validate the aligned episode ───────────────────────

echo "--- 7. align_episode / profile_aligned_episode / validate_aligned_episode ---"
ALIGN_PAYLOAD="$(cat <<JSON
{
  "type": "align_episode",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "episode_id": "$EPISODE_ID",
    "alignment_config": {
      "target_frequency_hz": $ALIGN_FREQUENCY_HZ,
      "tolerance_us": 200000,
      "max_gap_us": 2000000
    },
    "source_context": {"source_clock": "mcap_log_time"}
  }
}
JSON
)"
ALIGN_CREATE_RESP="$(create_job "$API_BASE_URL" "$ALIGN_PAYLOAD")"
ALIGN_JOB_ID="$(extract_job_id "$ALIGN_CREATE_RESP")"
execute_job "$API_BASE_URL" "$ALIGN_JOB_ID" >/dev/null
ALIGN_JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$ALIGN_JOB_ID" "$POLL_TIMEOUT" 3)"
assert_job_succeeded "$ALIGN_JOB_JSON" "align_episode should succeed"
ALIGNED_ARTIFACT_ID="$(echo "$ALIGN_JOB_JSON" | jq -r '.job.result.aligned_artifact_id')"
echo "  aligned_artifact_id=$ALIGNED_ARTIFACT_ID"

for job_type in profile_aligned_episode validate_aligned_episode; do
  STAGE_PAYLOAD="$(cat <<JSON
{
  "type": "$job_type",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {"episode_id": "$EPISODE_ID", "aligned_artifact_id": "$ALIGNED_ARTIFACT_ID"}
}
JSON
)"
  STAGE_CREATE_RESP="$(create_job "$API_BASE_URL" "$STAGE_PAYLOAD")"
  STAGE_JOB_ID="$(extract_job_id "$STAGE_CREATE_RESP")"
  execute_job "$API_BASE_URL" "$STAGE_JOB_ID" >/dev/null
  STAGE_JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$STAGE_JOB_ID" "$POLL_TIMEOUT" 3)"
  assert_job_succeeded "$STAGE_JOB_JSON" "$job_type should succeed"
done
echo "  OK"
echo ""

# ── 8. export_learning_data (v2-sharded) ─────────────────────────────────────

echo "--- 8. export_learning_data ---"
EXPORT_PAYLOAD="$(cat <<JSON
{
  "type": "export_learning_data",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {"inputs": [{"episode_id": "$EPISODE_ID", "aligned_artifact_id": "$ALIGNED_ARTIFACT_ID"}]}
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
echo "  manifest_artifact_id=$EXPORT_MANIFEST_ARTIFACT_ID episode_count=$EXPORT_EPISODE_COUNT"
[ -n "$EXPORT_MANIFEST_ARTIFACT_ID" ] && [ "$EXPORT_MANIFEST_ARTIFACT_ID" != "null" ] || {
  echo "❌ Expected a manifest_artifact_id from export_learning_data" >&2
  exit 1
}
echo ""

# ── 9. SceneOpsDataset.open() + step reads (reused, unmodified script) ──────

echo "--- 9. SceneOpsDataset.open() + step reads (scripts/canonical/verify_learning_export.py) ---"
ARTIFACTS_JSON="$(fetch_artifacts_by_owner "$API_BASE_URL" "dataset_version" "$DATASET_ID:$DATASET_VERSION")"
MANIFEST_ARTIFACT="$(echo "$ARTIFACTS_JSON" | jq '[.artifacts[] | select(.kind == "learning_data_export_manifest")][0]')"
MANIFEST_URI="$(echo "$MANIFEST_ARTIFACT" | jq -r '.uri')"
MANIFEST_CHECKSUM="$(echo "$MANIFEST_ARTIFACT" | jq -r '.checksum')"
[ -n "$MANIFEST_URI" ] && [ "$MANIFEST_URI" != "null" ] || {
  echo "❌ Could not resolve the learning_data_export_manifest artifact's uri" >&2
  exit 1
}

MINIO_ENDPOINT_URL="http://localhost:${MINIO_API_PORT:-9000}" \
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}" \
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}" \
MINIO_BUCKET="${MINIO_BUCKET:-sceneops}" \
uv run python scripts/canonical/verify_learning_export.py \
  --manifest-uri "$MANIFEST_URI" \
  --manifest-checksum "$MANIFEST_CHECKSUM" \
  --expected-episode-count "$EXPORT_EPISODE_COUNT" \
  --observation-channels "$OBSERVATION_CHANNELS" \
  --action-channels "$ACTION_CHANNELS" \
  --open-dataset
echo ""

# ── 10. canonical source MCAP remains unchanged ──────────────────────────────

echo "--- 10. canonical source MCAP unchanged ---"
ROBOT_RUN_ARTIFACTS_JSON="$(fetch_artifacts_by_owner "$API_BASE_URL" "robot_run" "$ROBOT_RUN_ID")"
STORED_CHECKSUM="$(echo "$ROBOT_RUN_ARTIFACTS_JSON" | jq -r '.artifacts[0].checksum')"
if [ "$STORED_CHECKSUM" != "$EXPECTED_CHECKSUM" ]; then
  echo "❌ RobotRun's own ArtifactRecord checksum changed: stored=$STORED_CHECKSUM expected=$EXPECTED_CHECKSUM" >&2
  exit 1
fi
echo "  ✅  checksum unchanged ($STORED_CHECKSUM)"
echo ""

# ── 11. retry: re-dispatch raw_log_episode_building for the same RobotRun ───
#       (existing force/idempotency semantics only -- no new retry model)

echo "--- 11. retry: re-dispatch raw_log_episode_building (force=true) ---"
RETRY_CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$BUILD_PAYLOAD")"
RETRY_PIPELINE_RUN_ID="$(extract_pipeline_run_id "$RETRY_CREATE_RESP")"
dispatch_pipeline_run "$API_BASE_URL" "$RETRY_PIPELINE_RUN_ID" >/dev/null
RETRY_PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$RETRY_PIPELINE_RUN_ID" "$POLL_TIMEOUT" 3)"
assert_pipeline_succeeded "$RETRY_PIPELINE_JSON" "raw_log_episode_building retry should succeed" "$API_BASE_URL" "$RETRY_PIPELINE_RUN_ID"
echo "  ✅  retry succeeded (materialization re-ran; canonical MCAP untouched)"

RETRY_ROBOT_RUN_ARTIFACTS_JSON="$(fetch_artifacts_by_owner "$API_BASE_URL" "robot_run" "$ROBOT_RUN_ID")"
RETRY_CHECKSUM="$(echo "$RETRY_ROBOT_RUN_ARTIFACTS_JSON" | jq -r '.artifacts[0].checksum')"
[ "$RETRY_CHECKSUM" = "$EXPECTED_CHECKSUM" ] || {
  echo "❌ RobotRun's ArtifactRecord checksum changed after retry: $RETRY_CHECKSUM" >&2
  exit 1
}
echo ""

echo "=== PASSED ==="
echo "  robot_run_id=$ROBOT_RUN_ID"
echo "  dataset_id=$DATASET_ID dataset_version=$DATASET_VERSION"
echo "  episode_id=$EPISODE_ID"
echo "  manifest_artifact_id=$EXPORT_MANIFEST_ARTIFACT_ID"
