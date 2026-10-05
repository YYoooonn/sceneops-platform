#!/usr/bin/env bash
# canonical_verify.sh — read-only check that a canonical L1/L2 baseline is
# intact: RobotRuns registered with pinned recordings, Scenes and Episodes
# registered at manifest revisions their ArtifactRecords carry, every unit
# validated, profiled and ready at its current revision, and the DatasetVersion
# summaries equal to the registered membership. Creates and mutates nothing.
#
# Same identity variables as canonical_bootstrap.sh (BASELINE_ID, SOURCE_UNITS,
# DATASET_ID, DATASET_VERSION). Prints one JSON summary on stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/scripts/e2e/lib.sh"
source "$SCRIPT_DIR/baseline_lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"

require_api "$API_BASE_URL"
baseline_verify "$API_BASE_URL"
