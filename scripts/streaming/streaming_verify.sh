#!/usr/bin/env bash
# streaming_verify.sh — read-only check that a streaming reference baseline is
# intact (see streaming_bootstrap.sh): exactly the selected fixtures' streamed
# RobotRuns registered under the baseline's robot, each a Kafka capture of its
# fixture whose registered manifest carries the locked recording's message count
# and per-channel counts; one Scene and one Episode per RobotRun, each registered
# at the manifest revision its ArtifactRecord carries and validated, profiled and
# ready at its current revision; DatasetVersion summaries equal to the registered
# membership. Creates and mutates nothing; replays nothing.
#
# Same selection and identity variables as streaming_bootstrap.sh
# (REFERENCE_SCOPE, FIXTURE, BASELINE_ID). Prints one JSON summary on stdout,
# identical to the one the bootstrap printed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/e2e/lib.sh"
BASELINE_PREFIX="${BASELINE_PREFIX:-stream-ref}"
source "$REPO_ROOT/scripts/canonical/baseline_lib.sh"
API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
source "$SCRIPT_DIR/streaming_lib.sh"

require_api "$API_BASE_URL"
baseline_resolve lock-only
streaming_verify "$API_BASE_URL"
