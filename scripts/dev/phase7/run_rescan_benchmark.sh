#!/usr/bin/env bash
# Phase 7.0 study: section 4 (rescan cost vs topic history) + a bonus
# partition-aware (assign()) comparison at partition-count=1, to isolate
# consumer-group-join overhead from pure rescan-volume cost. Real Kafka,
# real MCAP capture -- no mocking. Appends one JSON row per checkpoint to
# the given results file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

RESULTS_FILE="${1:?Usage: $0 RESULTS_FILE CHECKPOINT [CHECKPOINT...]}"
shift
CHECKPOINTS=("$@")

TOPIC="${PHASE7_BENCH_TOPIC:-sceneops.robot.telemetry.v1}"
TARGET_COUNT=3000
COMPOSE="docker compose --env-file .env.local"

export SCENEOPS_STREAMING_KAFKA_BOOTSTRAP_SERVERS="localhost:9092"

_end_offset() {
  docker exec sceneops-kafka-1 /opt/kafka/bin/kafka-get-offsets.sh \
    --bootstrap-server localhost:9092 --topic "$TOPIC" --time -1 2>/dev/null \
    | awk -F: '{print $3}'
}

_lag() {
  local group_id="$1"
  docker exec sceneops-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 --describe --group "$group_id" 2>/dev/null \
    | awk '$1 != "GROUP" && $6 ~ /^[0-9-]+$/ { sum += $6 } END { print (sum=="" ? "null" : sum) }'
}

for CHECKPOINT in "${CHECKPOINTS[@]}"; do
  CURRENT="$(_end_offset)"
  NEEDED=$((CHECKPOINT - CURRENT))
  echo "=== checkpoint=$CHECKPOINT current_offset=$CURRENT needed_noise=$NEEDED ===" >&2
  if [ "$NEEDED" -gt 0 ]; then
    uv run python scripts/dev/phase7/producer.py --topic "$TOPIC" noise --count "$NEEDED" --num-runs 200 \
      --run-prefix "phase7-rescan-noise" >&2
  fi
  HISTORY_BEFORE_TARGET="$(_end_offset)"

  ROBOT_RUN_ID="phase7-rescan-$CHECKPOINT-$(date +%s)"
  PRODUCE_JSON="$(uv run python scripts/dev/phase7/producer.py --topic "$TOPIC" run \
    --robot-run-id "$ROBOT_RUN_ID" --count "$TARGET_COUNT")"
  echo "  produce: $PRODUCE_JSON" >&2
  TARGET_PARTITION="$(jq -r '.partition' <<<"$PRODUCE_JSON")"

  GROUP_ID="sceneops-mcap-capture-study-lookup"  # placeholder; real group computed below

  CAPTURE_JSON="$($COMPOSE --profile ros2 run --rm ros2 python3 \
    /workspace/scripts/dev/phase7/capture_runner.py --output-root /data/tmp_phase7_bench --topic "$TOPIC" \
    runscoped --robot-run-id "$ROBOT_RUN_ID" --count "$TARGET_COUNT")"
  echo "  runscoped capture: $CAPTURE_JSON" >&2
  GROUP_ID="$(jq -r '.group_id' <<<"$CAPTURE_JSON")"
  LAG_AFTER="$(_lag "$GROUP_ID")"

  # Bonus: partition-aware (assign()) capture of a SECOND target run at the
  # same history checkpoint, on the same (single) partition -- isolates
  # consumer-group-join overhead from pure scan-volume cost (section 6
  # covers the true multi-partition case on an isolated topic).
  ROBOT_RUN_ID_PA="phase7-rescan-pa-$CHECKPOINT-$(date +%s)"
  uv run python scripts/dev/phase7/producer.py --topic "$TOPIC" run \
    --robot-run-id "$ROBOT_RUN_ID_PA" --count "$TARGET_COUNT" >&2
  PA_JSON="$($COMPOSE --profile ros2 run --rm ros2 python3 \
    /workspace/scripts/dev/phase7/capture_runner.py --output-root /data/tmp_phase7_bench --topic "$TOPIC" \
    partitionaware --robot-run-id "$ROBOT_RUN_ID_PA" --count "$TARGET_COUNT" --partition 0)"
  echo "  partitionaware capture: $PA_JSON" >&2

  jq -n \
    --arg checkpoint "$CHECKPOINT" \
    --arg history_before_target "$HISTORY_BEFORE_TARGET" \
    --argjson produce "$PRODUCE_JSON" \
    --argjson runscoped "$CAPTURE_JSON" \
    --argjson partitionaware "$PA_JSON" \
    --argjson lag_after_runscoped "${LAG_AFTER:-null}" \
    '{checkpoint: ($checkpoint|tonumber), history_before_target: ($history_before_target|tonumber), produce: $produce, runscoped: $runscoped, partitionaware_p1: $partitionaware, lag_after_runscoped: $lag_after_runscoped}' \
    >> "$RESULTS_FILE"
  echo "  row appended to $RESULTS_FILE" >&2
done
