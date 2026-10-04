#!/usr/bin/env bash
# e2e_robot_learning.sh
#
# The canonical robot-learning-domain E2E -- composes the real, existing
# chain end to end, over one or more real nuScenes scenes:
#
#   real nuScenes CAN bus
#     -> CanReplayNode (real rclpy, ros2/ Docker sandbox)
#     -> ros2 bag record --storage mcap
#     -> real rosbag2/MCAP file
#     -> RosbagAdapter (real CDR decode)
#     -> raw_log_episode_building pipeline -> EpisodeRecord
#     -> align_episode (real TemporalAlignmentConfig)
#     -> profile_aligned_episode / validate_aligned_episode
#     -> export_learning_data (v2-sharded write path)
#     -> curate_episodes
#
# This uses the SAME real CAN->ROS2->MCAP->RosbagAdapter path
# e2e_robot_can_replay.sh/e2e_episode_building.sh/e2e_episode_curation.sh
# exercise piecemeal -- nothing here is a generated/synthetic episode
# fixture. Those three scripts remain available standalone for
# stage-by-stage debugging (see makefiles/e2e.mk's "Debug / Stage" section)
# but this composed flow is the primary documented operator path, and it
# fixes the two hidden-dependency issues the individual stages had when run
# by hand:
#   (a) the MCAP existence check happens before any Robot/RobotRun/
#       DatasetVersion API call (via lib.sh's require_mcap_file), for every
#       selected scene;
#   (b) EPISODE_IDs feeding align/profile/validate/export/curate are the
#       REAL values captured from build_episodes' own job/task result in
#       this same process -- never reconstructed by reimplementing
#       build_episodes.py's ID-formatting formula in bash;
#   (c) the RobotRun is created through the Recording Publisher +
#       REGISTER_ROBOT_RUN (lib.sh's publish_and_register_robot_run), the
#       only RobotRun creation path; build_episodes resolves the recording
#       through the RobotRun's registered recording ArtifactRecord.
#
# Curation policy is deliberately the empty/unrestricted CurationPolicy
# ("no restriction on any dimension" -- see
# packages/sceneops-core/sceneops_core/episodes/curation/policy.py's own
# docstring): this is a real-data ACCEPTANCE run proving the whole chain
# persists a usable result, not the curation-policy-mechanism test
# e2e_episode_curation.sh already owns (which deliberately forces one
# revision to be rejected via two different alignment configs of the same
# episode -- keep using that script directly if you want to re-verify
# selection/rejection mechanics specifically).
#
# ── Selection semantics ───────────────────────────────────────────────────
#
#   SCENE=scene-0061           run only this one scene (still verified to
#                              have real CAN-bus data -- pose/ms_imu/
#                              vehicle_monitor -- before anything runs)
#   MAX_SCENES=3               select the first N nuScenes v1.0-mini scenes
#                              (sorted, deterministic) for which real
#                              CAN-bus data exists; scenes without it are
#                              logged and skipped, never silently dropped
#   (neither given)            defaults to MAX_SCENES=1 -- one scene
#                              (scene-0061, the first eligible one), a
#                              small deterministic default for local E2E use
#   (both given)                fails fast with a clear error -- no implicit
#                              precedence between SCENE and MAX_SCENES
#
# Usage:
#   bash scripts/e2e/e2e_robot_learning.sh
#   bash scripts/e2e/e2e_robot_learning.sh SCENE=scene-0061
#   bash scripts/e2e/e2e_robot_learning.sh MAX_SCENES=3
#
# Prereq: the ROS2 sandbox image (`make ros2-up`, `--profile ros2`) and the
# nuScenes CAN bus expansion unzipped at data/raw/nuscenes/can_bus/.
#
# Env overrides:
#   API_BASE_URL      (default: http://localhost:8000)
#   ROBOT_ID          (default: robot-nuscenes-01)
#   DATASET_ID / DATASET_VERSION  (default from the shared "core" E2E fixture)
#   RATE              CanReplayNode playback speed multiplier (default: 10.0)
#   RECORD_DURATION   seconds to bound `ros2 bag record` (default: 30)
#   ALIGN_FREQUENCY_HZ  align_episode target_frequency_hz (default: 5.0)
#   POLL_TIMEOUT      max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"
unavailable_until e2e-robot-learning 11 \
  "Episodes are built only by recording_episode_building from a registered RobotRun (make e2e-recording-episode); this workflow used the removed build_episodes / raw_log_episode_building path and is rebuilt on canonical Episodes in the step-11 consolidation"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
resolve_e2e_fixture core
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-01}"
RATE="${RATE:-10.0}"
RECORD_DURATION="${RECORD_DURATION:-30}"
ALIGN_FREQUENCY_HZ="${ALIGN_FREQUENCY_HZ:-5.0}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"
NUSCENES_ROOT="${NUSCENES_ROOT:-$REPO_ROOT/data/raw/nuscenes}"

COMPOSE="docker compose -f $REPO_ROOT/compose.yaml"

# ── 0. Conflict handling + deterministic scene selection ────────────────────

if [ -n "${SCENE:-}" ] && [ -n "${MAX_SCENES:-}" ]; then
  echo "❌ Provide either SCENE or MAX_SCENES, not both (got SCENE=$SCENE MAX_SCENES=$MAX_SCENES)." >&2
  echo "   SCENE runs exactly one named scene; MAX_SCENES selects the first N eligible ones." >&2
  exit 1
fi

SCENE_JSON_PATH="$NUSCENES_ROOT/v1.0-mini/scene.json"
CAN_BUS_DIR="$NUSCENES_ROOT/can_bus"

if [ ! -f "$SCENE_JSON_PATH" ]; then
  echo "❌ nuScenes v1.0-mini scene.json not found at $SCENE_JSON_PATH" >&2
  exit 1
fi

list_all_scene_names() {
  jq -r '.[].name' "$SCENE_JSON_PATH" | sort
}

# A scene is "eligible" for this E2E iff the CAN bus expansion has all three
# message types e2e_robot_learning.sh's replay depends on (docs/workflows/
# robot-run-and-mcap.md §2: pose/ms_imu/vehicle_monitor). Every skip is
# logged with its reason -- never silently dropped.
list_eligible_scene_names() {
  local name
  while read -r name; do
    if [ -f "$CAN_BUS_DIR/${name}_pose.json" ] \
      && [ -f "$CAN_BUS_DIR/${name}_ms_imu.json" ] \
      && [ -f "$CAN_BUS_DIR/${name}_vehicle_monitor.json" ]; then
      echo "$name"
    else
      echo "  ⚠ skipping $name — missing CAN bus file(s) (pose/ms_imu/vehicle_monitor) under $CAN_BUS_DIR" >&2
    fi
  done < <(list_all_scene_names)
}

TOTAL_SCENE_COUNT="$(list_all_scene_names | wc -l | tr -d ' ')"

echo "=== e2e-robot-learning ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION  ROBOT_ID=$ROBOT_ID"
echo ""
echo "--- 0. Select scenes (CAN-bus eligibility check) ---"
echo "  nuScenes v1.0-mini total scenes: $TOTAL_SCENE_COUNT"

# Captured ONCE via command substitution -- never piped live into a
# short-circuiting consumer (`grep -q`, `head -n`). Under `set -o pipefail`
# (this script's own shebang line), a consumer that exits before draining
# its producer sends the producer SIGPIPE, which pipefail then reports as
# pipeline failure EVEN THOUGH the consumer itself found what it was
# looking for -- e.g. `list_eligible_scene_names | grep -qx "$SCENE"`
# spuriously reported "not found" for a real match once `grep -q` returned
# before the producer's slower per-file eligibility checks had finished
# writing every line. Command substitution always drains its subshell to
# completion first, so operating on the captured string afterward
# (here-strings, not pipes) is race-free.
ELIGIBLE_SCENES="$(list_eligible_scene_names)"
ELIGIBLE_COUNT="$(wc -l <<< "$ELIGIBLE_SCENES" | tr -d ' ')"
[ -n "$ELIGIBLE_SCENES" ] || ELIGIBLE_COUNT=0

declare -a SCENES
if [ -n "${SCENE:-}" ]; then
  if ! grep -qx "$SCENE" <<< "$ELIGIBLE_SCENES"; then
    echo "❌ SCENE=$SCENE has no CAN-bus data (pose/ms_imu/vehicle_monitor) under $CAN_BUS_DIR — cannot run e2e-robot-learning against it." >&2
    exit 1
  fi
  SCENES=("$SCENE")
  echo "  mode=explicit SCENE  selected=$SCENE"
else
  REQUESTED_N="${MAX_SCENES:-1}"
  while read -r eligible_name; do
    [ -n "$eligible_name" ] && SCENES+=("$eligible_name")
  done < <(head -n "$REQUESTED_N" <<< "$ELIGIBLE_SCENES")
  echo "  mode=MAX_SCENES  eligible=$ELIGIBLE_COUNT/$TOTAL_SCENE_COUNT  requested=$REQUESTED_N  selected=${#SCENES[@]}"
  if [ "${#SCENES[@]}" -eq 0 ]; then
    echo "❌ No CAN-bus-eligible scenes found under $NUSCENES_ROOT" >&2
    exit 1
  fi
  if [ "${#SCENES[@]}" -lt "$REQUESTED_N" ]; then
    echo "  ⚠ only ${#SCENES[@]} eligible scene(s) available — fewer than requested MAX_SCENES=$REQUESTED_N"
  fi
fi
echo "  scenes selected (deterministic, sorted): ${SCENES[*]}"
echo ""

# ── 1. Register Dataset/DatasetVersion once (Episode summary lives here) ────

echo "--- 1. Upsert Dataset/DatasetVersion (shared across all selected scenes) ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "Robot learning E2E" | jq '.dataset | {datasetId}'
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" \
  | jq '.version | {datasetId, version, status, episode}'
echo ""

# ── 2. Build the ROS2 sandbox image once ─────────────────────────────────────

echo "--- 2. Build ROS2 sandbox image ---"
$COMPOSE --profile ros2 build ros2 >/dev/null
echo "  OK"
echo ""

# ── 3. Per-scene: record real CAN replay -> MCAP, build the Episode ─────────

declare -a ALL_EPISODE_IDS=()

for SCENE_NAME in "${SCENES[@]}"; do
  echo "=== Scene: $SCENE_NAME ==="

  RUN_ID="run-${SCENE_NAME}-episodes"
  BAG_DIR="/data/raw/rosbag/${SCENE_NAME}"
  MCAP_URI="${BAG_DIR}/${SCENE_NAME}_0.mcap"

  echo "--- 3a. Record CAN replay (ros2 Docker sandbox) ---"
  rm -rf "${REPO_ROOT}${BAG_DIR}"
  $COMPOSE --profile ros2 run --rm ros2 sh -c " \
    timeout $RECORD_DURATION ros2 bag record -o $BAG_DIR --storage mcap \
      /vehicle/odom /vehicle/imu /vehicle/status /vehicle/control /mission/status & \
    sleep 2; \
    python3 /workspace/nodes/can_replay_node.py --scene $SCENE_NAME --rate $RATE; \
    wait \
  "
  # Fail fast, before any Robot/RobotRun/DatasetVersion API call for this
  # scene (item 7A) -- the DatasetVersion upsert above is shared/idempotent
  # and already done once; this check gates everything scene-specific below.
  require_mcap_file "$REPO_ROOT" "$MCAP_URI"
  echo "  bag=${MCAP_URI}  OK"
  echo ""

  echo "--- 3b. Register Robot + RobotRun (artifact-backed) ---"
  upsert_robot "$API_BASE_URL" "$ROBOT_ID" "nuscenes-can-replay" | jq '.robot | {robotId, status}'
  publish_and_register_robot_run "$REPO_ROOT" "$API_BASE_URL" \
    "$ROBOT_ID" "$RUN_ID" "$MCAP_URI" ros2_bag "nuscenes-can-replay" \
    | jq '.job.result | {run_id, created, manifest_checksum}'
  echo ""

  echo "--- 3c. Dispatch raw_log_episode_building pipeline ---"
  BUILD_PAYLOAD="$(cat <<JSON
{
  "type": "raw_log_episode_building",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "build_episodes": {
      "robot_run_id": "$RUN_ID",
      "segmentation": {"strategy": "mission_boundary"}
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
  BUILD_PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$BUILD_PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
  assert_pipeline_succeeded "$BUILD_PIPELINE_JSON" "raw_log_episode_building should succeed for $SCENE_NAME" "$API_BASE_URL" "$BUILD_PIPELINE_RUN_ID"

  BUILD_TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$BUILD_PIPELINE_RUN_ID")"
  BUILD_TASK="$(echo "$BUILD_TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "build_episodes")')"
  # Explicit hand-off (item 7B): episode_ids come from this real job result,
  # never reconstructed by reimplementing build_episodes.py's own ID-
  # formatting formula in bash.
  SCENE_EPISODE_IDS_JSON="$(echo "$BUILD_TASK" | jq -c '.result.rawResult.episode_ids')"
  SCENE_EPISODE_COUNT="$(echo "$SCENE_EPISODE_IDS_JSON" | jq 'length')"
  echo "  episode_count=$SCENE_EPISODE_COUNT  episode_ids=$SCENE_EPISODE_IDS_JSON"

  [ "${SCENE_EPISODE_COUNT:-0}" -ge 1 ] || {
    echo "❌ build_episodes produced 0 episodes for $SCENE_NAME" >&2
    exit 1
  }

  while read -r one_episode_id; do
    ALL_EPISODE_IDS+=("$one_episode_id")
  done < <(echo "$SCENE_EPISODE_IDS_JSON" | jq -r '.[]')
  echo "  OK"
  echo ""
done

echo "=== All scenes built: ${#ALL_EPISODE_IDS[@]} episode(s) total: ${ALL_EPISODE_IDS[*]} ==="
echo ""

# ── 4. Per-episode: align (real TemporalAlignmentConfig) + profile + validate ──

declare -a EXPORT_INPUTS=()

for EPISODE_ID in "${ALL_EPISODE_IDS[@]}"; do
  echo "--- 4. Align/profile/validate episode $EPISODE_ID (target_frequency_hz=$ALIGN_FREQUENCY_HZ) ---"

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
  assert_job_succeeded "$ALIGN_JOB_JSON" "align_episode should succeed for $EPISODE_ID"
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
    assert_job_succeeded "$STAGE_JOB_JSON" "$job_type should succeed for $EPISODE_ID"
  done

  EXPORT_INPUTS+=("{\"episode_id\": \"$EPISODE_ID\", \"aligned_artifact_id\": \"$ALIGNED_ARTIFACT_ID\"}")
  echo "  OK"
  echo ""
done

# ── 5. EXPORT_LEARNING_DATA (v2-sharded write path, all episodes) ───────────

echo "--- 5. EXPORT_LEARNING_DATA (${#EXPORT_INPUTS[@]} episode(s)) ---"
EXPORT_INPUTS_JSON="[$(IFS=,; echo "${EXPORT_INPUTS[*]}")]"
EXPORT_PAYLOAD="$(jq -n --argjson inputs "$EXPORT_INPUTS_JSON" --arg d "$DATASET_ID" --arg v "$DATASET_VERSION" \
  '{type: "export_learning_data", dataset_id: $d, dataset_version: $v, force: true, params: {inputs: $inputs}}')"

EXPORT_CREATE_RESP="$(create_job "$API_BASE_URL" "$EXPORT_PAYLOAD")"
EXPORT_JOB_ID="$(extract_job_id "$EXPORT_CREATE_RESP")"
execute_job "$API_BASE_URL" "$EXPORT_JOB_ID" >/dev/null
EXPORT_JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$EXPORT_JOB_ID" "$POLL_TIMEOUT" 3)"
assert_job_succeeded "$EXPORT_JOB_JSON" "export_learning_data should succeed"

EXPORT_ID="$(echo "$EXPORT_JOB_JSON" | jq -r '.job.result.export_id')"
EXPORT_MANIFEST_ARTIFACT_ID="$(echo "$EXPORT_JOB_JSON" | jq -r '.job.result.manifest_artifact_id')"
EXPORT_EPISODE_COUNT="$(echo "$EXPORT_JOB_JSON" | jq -r '.job.result.episode_count')"
EXPORT_SHARD_COUNTS="$(echo "$EXPORT_JOB_JSON" | jq -c '.job.result.shard_counts')"
echo "  export_id=$EXPORT_ID"
echo "  manifest_artifact_id=$EXPORT_MANIFEST_ARTIFACT_ID"
echo "  episode_count=$EXPORT_EPISODE_COUNT"
echo "  shard_counts=$EXPORT_SHARD_COUNTS"
[ -n "$EXPORT_MANIFEST_ARTIFACT_ID" ] && [ "$EXPORT_MANIFEST_ARTIFACT_ID" != "null" ] || {
  echo "❌ Expected a manifest_artifact_id from export_learning_data" >&2
  exit 1
}
echo "  OK"
echo ""

# ── 6. CURATE_EPISODES (unrestricted policy -- an acceptance run, not a ─────
#      curation-mechanism test; see header)

echo "--- 6. CURATE_EPISODES (unrestricted policy) ---"
CURATE_PAYLOAD="$(cat <<JSON
{
  "type": "curate_episodes",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "learning_data_export_manifest_artifact_id": "$EXPORT_MANIFEST_ARTIFACT_ID",
    "policy": {}
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
echo "  candidate_count=$CANDIDATE_COUNT  selected_count=$SELECTED_COUNT  rejected_count=$REJECTED_COUNT"
[ "$CANDIDATE_COUNT" = "${#ALL_EPISODE_IDS[@]}" ] || {
  echo "❌ Expected candidate_count=${#ALL_EPISODE_IDS[@]}, got $CANDIDATE_COUNT" >&2
  exit 1
}
[ "$SELECTED_COUNT" = "$CANDIDATE_COUNT" ] && [ "$REJECTED_COUNT" = "0" ] || {
  echo "❌ Expected every real, structurally-valid episode selected under an unrestricted policy (selected=$SELECTED_COUNT rejected=$REJECTED_COUNT of $CANDIDATE_COUNT)" >&2
  exit 1
}
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  scenes=${SCENES[*]}"
echo "  episode_ids=${ALL_EPISODE_IDS[*]}"
echo "  export_id=$EXPORT_ID  manifest_artifact_id=$EXPORT_MANIFEST_ARTIFACT_ID"
echo "  curation_id=$CURATION_ID  selected=$SELECTED_COUNT/$CANDIDATE_COUNT"
