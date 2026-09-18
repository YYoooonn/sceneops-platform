#!/usr/bin/env bash
# e2e_robot_can_replay.sh
#
# E2E test for the full roadmap Phase 4 (ROS2 Robot Data Source) chain:
#   nuScenes CAN bus data -> ros2/nodes/can_replay_node.py (real rclpy
#   publisher, run inside the ros2 Docker sandbox) -> `ros2 bag record
#   --storage mcap` -> ingest_robot_states job (RosbagAdapter ->
#   RobotState/Mission rows).
#
# Requires data/raw/nuscenes/can_bus/ to exist locally (see docs on where to
# unzip the nuScenes CAN bus expansion).
#
# Registration (POST /robots, POST /robot-runs) and verification (GET
# /missions, GET /robot-states) both go through the real API —
# apps/api/app/domains/robots/. `sceneops-worker robots register-run` also
# exists as a CLI alternative to the same POST endpoints.
#
# Usage:
#   bash scripts/e2e/e2e_robot_can_replay.sh
#
# Env overrides:
#   API_BASE_URL     (default: http://localhost:8000)
#   SCENE            nuScenes scene name to replay (default: scene-0061)
#   RATE             CanReplayNode playback speed multiplier (default: 10.0)
#   RECORD_DURATION  seconds to bound `ros2 bag record` (default: 30)
#   ROBOT_ID         (default: robot-nuscenes-01)
#   POLL_TIMEOUT     max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
SCENE="${SCENE:-scene-0061}"
RATE="${RATE:-10.0}"
RECORD_DURATION="${RECORD_DURATION:-30}"
ROBOT_ID="${ROBOT_ID:-robot-nuscenes-01}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

RUN_ID="run-${SCENE}"
BAG_DIR="/data/raw/rosbag/${SCENE}"
MCAP_URI="${BAG_DIR}/${SCENE}_0.mcap"

COMPOSE="docker compose -f $REPO_ROOT/docker-compose.local.yml"

echo "=== robot_can_replay (Phase 4) E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  SCENE=$SCENE  RATE=$RATE  ROBOT_ID=$ROBOT_ID  RUN_ID=$RUN_ID"
echo ""

# ── 1. Record a real CAN replay bag ──────────────────────────────────────────

echo "--- 1. Record CAN replay (ros2 Docker sandbox) ---"
$COMPOSE --profile ros2 build ros2 >/dev/null
rm -rf "${REPO_ROOT}${BAG_DIR}"
$COMPOSE --profile ros2 run --rm ros2 sh -c " \
  timeout $RECORD_DURATION ros2 bag record -o $BAG_DIR --storage mcap \
    /vehicle/odom /vehicle/imu /vehicle/status /vehicle/control /mission/status & \
  sleep 2; \
  python3 /workspace/nodes/can_replay_node.py --scene $SCENE --rate $RATE; \
  wait \
"

if [ ! -f "${REPO_ROOT}${MCAP_URI}" ]; then
  echo "❌ Expected bag file not found: ${REPO_ROOT}${MCAP_URI}" >&2
  exit 1
fi
echo "  bag=${MCAP_URI}"
echo "  OK"
echo ""

# ── 2. Register Robot + RobotRun via API ─────────────────────────────────────

echo "--- 2. Register Robot + RobotRun ---"
upsert_robot "$API_BASE_URL" "$ROBOT_ID" "nuscenes-can-replay" \
  | jq '.robot | {robotId, status}'
upsert_robot_run "$API_BASE_URL" "$RUN_ID" "$ROBOT_ID" "$MCAP_URI" \
  | jq '.robotRun | {runId, robotId, mcapUri}'
echo ""

# ── 3. Create ingest_robot_states job ────────────────────────────────────────

echo "--- 3. Create job ---"
PAYLOAD="$(cat <<JSON
{
  "type": "ingest_robot_states",
  "force": true,
  "params": {
    "robot_id": "$ROBOT_ID",
    "robot_run_id": "$RUN_ID"
  }
}
JSON
)"

CREATE_RESP="$(create_job "$API_BASE_URL" "$PAYLOAD")"
JOB_ID="$(extract_job_id "$CREATE_RESP")"
echo "  job_id=$JOB_ID"
echo ""

# ── 4. Dispatch ───────────────────────────────────────────────────────────────

echo "--- 4. Dispatch ---"
EXEC_RESP="$(execute_job "$API_BASE_URL" "$JOB_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 5. Poll ───────────────────────────────────────────────────────────────────

echo "--- 5. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
JOB_JSON="$(poll_job_terminal "$API_BASE_URL" "$JOB_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 6. Assert job succeeded ───────────────────────────────────────────────────

echo "--- 6. Assert job ---"
FINAL_STATUS="$(echo "$JOB_JSON" | jq -r '.job.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" = "failed" ]; then
  echo "  error=$(echo "$JOB_JSON" | jq -r '.job.error.message // "unknown"')"
fi

assert_job_succeeded "$JOB_JSON" 'ingest_robot_states job should succeed'
echo "  OK"
echo ""

# ── 7. Assert states + missions were ingested ────────────────────────────────

echo "--- 7. Assert result counts ---"
STATE_COUNT="$(echo "$JOB_JSON" | jq -r '.job.result.state_count // 0')"
MISSION_COUNT="$(echo "$JOB_JSON" | jq -r '.job.result.mission_count // 0')"
echo "  state_count=$STATE_COUNT  mission_count=$MISSION_COUNT"

assert_json_gt "$JOB_JSON" '.job.result.state_count' 0 'expected state_count > 0'
assert_json_gt "$JOB_JSON" '.job.result.mission_count' 0 'expected mission_count > 0'
echo "  OK"
echo ""

# ── 8. Verify via GET /missions and /robot-states (not just the job result) ──

echo "--- 8. Verify persisted rows via API ---"
MISSIONS_JSON="$(fetch_missions "$API_BASE_URL" "$RUN_ID")"
MISSION_STATUS="$(echo "$MISSIONS_JSON" | jq -r '.missions[0].status // empty')"
echo "  mission status=$MISSION_STATUS"
[ "$MISSION_STATUS" = "completed" ] || { echo "❌ Expected mission status=completed, got $MISSION_STATUS" >&2; exit 1; }

STATES_JSON="$(fetch_robot_states "$API_BASE_URL" "$RUN_ID" 1)"
API_STATE_COUNT="$(echo "$STATES_JSON" | jq -r '.count')"
echo "  robot_states via API (limit=1 page)=$API_STATE_COUNT, first state:"
echo "$STATES_JSON" | jq '.robotStates[0] | {stateId, timestampUs, position, orientation}'
[ "${API_STATE_COUNT:-0}" -ge 1 ] || { echo "❌ Expected at least 1 robot_state via API" >&2; exit 1; }
echo "  OK"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  job_id=$JOB_ID"
echo "  robot_id=$ROBOT_ID  run_id=$RUN_ID"
echo "  bag=${MCAP_URI}"
echo "  state_count=$STATE_COUNT  mission_count=$MISSION_COUNT"
