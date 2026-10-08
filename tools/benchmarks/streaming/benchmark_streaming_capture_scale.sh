#!/usr/bin/env bash
# benchmark_streaming_capture_scale.sh — reproducible entry point for the
# Phase 6.6 streaming capture scale/backpressure benchmark
# (tools/benchmarks/streaming/benchmark_streaming_capture_scale.py). Real Kafka broker,
# real MCAP capture, no synthetic mocking.
#
# Runs the produce phase, queries real Kafka consumer-group lag from the
# HOST (this container has no docker socket to reach the kafka
# container's own kafka-consumer-groups.sh), runs the capture phase,
# queries lag again, prints one combined JSON report.
#
# Usage:
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 3000 64
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 30000 64
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 100000 64
#   tools/benchmarks/streaming/benchmark_streaming_capture_scale.sh 5000 1000000   # large payload

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

MESSAGE_COUNT="${1:?Usage: $0 MESSAGE_COUNT [PAYLOAD_BYTES]}"
PAYLOAD_BYTES="${2:-64}"
ROBOT_RUN_ID="bench-$(date +%s)-$$"

COMPOSE="docker compose --env-file .env.local"

# The run-scoped group (Phase 6.6.1) is derived from ROBOT_RUN_ID by the
# SAME shared helper (sceneops_recording.capture.group_id) run_capture() itself
# uses -- never reimplemented/guessed here. The produce-phase JSON below
# reports it (benchmark_streaming_capture_scale.py computes it via
# derive_capture_group_id), read via jq once that phase completes.
_lag() {
  local group_id="$1"
  docker exec sceneops-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 --describe --group "$group_id" 2>/dev/null \
    | awk '$1 != "GROUP" && $6 ~ /^[0-9-]+$/ { sum += $6 } END { print (sum=="" ? "null" : sum) }'
}

echo "=== benchmark: message_count=$MESSAGE_COUNT payload_bytes=$PAYLOAD_BYTES robot_run_id=$ROBOT_RUN_ID ===" >&2

PRODUCE_JSON="$($COMPOSE --profile streaming run --rm -T -v "$REPO_ROOT/tools/benchmarks:/workspace/tools/benchmarks:ro" --entrypoint /ros_entrypoint.sh capture python3 \
  /workspace/tools/benchmarks/streaming/benchmark_streaming_capture_scale.py \
  --phase produce --robot-run-id "$ROBOT_RUN_ID" \
  --message-count "$MESSAGE_COUNT" --payload-bytes "$PAYLOAD_BYTES")"
echo "  produce: $PRODUCE_JSON" >&2

GROUP_ID="$(jq -r '.group_id' <<<"$PRODUCE_JSON")"
echo "  group_id=$GROUP_ID" >&2

LAG_AFTER_PRODUCE="$(_lag "$GROUP_ID")"
echo "  kafka_lag_after_produce=$LAG_AFTER_PRODUCE" >&2

CAPTURE_JSON="$($COMPOSE --profile streaming run --rm -T -v "$REPO_ROOT/tools/benchmarks:/workspace/tools/benchmarks:ro" --entrypoint /ros_entrypoint.sh capture python3 \
  /workspace/tools/benchmarks/streaming/benchmark_streaming_capture_scale.py \
  --phase capture --robot-run-id "$ROBOT_RUN_ID" \
  --message-count "$MESSAGE_COUNT" --payload-bytes "$PAYLOAD_BYTES")"
echo "  capture: $CAPTURE_JSON" >&2

LAG_AFTER_CAPTURE="$(_lag "$GROUP_ID")"
echo "  kafka_lag_after_capture=$LAG_AFTER_CAPTURE" >&2

jq -n \
  --argjson produce "$PRODUCE_JSON" \
  --argjson capture "$CAPTURE_JSON" \
  --argjson lag_after_produce "$LAG_AFTER_PRODUCE" \
  --argjson lag_after_capture "$LAG_AFTER_CAPTURE" \
  '{produce: $produce, capture: $capture, kafka_lag_after_produce: $lag_after_produce, kafka_lag_after_capture: $lag_after_capture}'
