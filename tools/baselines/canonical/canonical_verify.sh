#!/usr/bin/env bash
# canonical_verify.sh — read-only check that a canonical L1/L2 baseline is
# intact: exactly the selected fixtures' RobotRuns registered, each pinning the
# recording the corpus lock names; Scenes and Episodes registered at manifest
# revisions their ArtifactRecords carry, every one belonging to those RobotRuns,
# validated, profiled and ready at its current revision; DatasetVersion summaries
# equal to the registered membership. Creates and mutates nothing; reads the lock
# but no recording.
#
# Same selection and identity variables as canonical_bootstrap.sh
# (REFERENCE_SCOPE, FIXTURE, BASELINE_ID, DATASET_ID, DATASET_VERSION). Prints one
# JSON summary on stdout, identical to the one the bootstrap printed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
source "$REPO_ROOT/tools/e2e/lib.sh"
source "$SCRIPT_DIR/baseline_lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"

require_api "$API_BASE_URL"
baseline_resolve lock-only
baseline_verify "$API_BASE_URL"
