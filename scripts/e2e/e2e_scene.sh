#!/usr/bin/env bash
# e2e_scene.sh
#
# The canonical Scene-domain E2E:
#   real nuScenes -> Integration Runtime -> Scene ingestion -> SceneRecord
#     -> validation -> profile -> scene index -> DatasetManifest
#
# dataset_scene_ingestion pipeline:
#   ingest_scenes -> register_scene -> validate_scene -> profile_scene
#   -> build_scene_index -> build_dataset_manifest
#
# Also owns the domain-relevant assertions for this same ingestion:
# validation_run_id/profile_run_id + their report URIs, dataset-version
# quality-cache readiness, the scene-summary cross-check, and
# optional-task-skip behavior -- all of these exercise the SAME real
# ingestion this script already runs, so they live here rather than in a
# second, largely-overlapping pipeline dispatch. Pipeline-definitions
# registry / unsupported-pipeline-type rejection need no nuScenes data at
# all and live in scripts/e2e/tests/test_pipeline_contracts_integration.py
# (make test-integration) instead.
#
# Usage:
#   bash scripts/e2e/e2e_scene.sh
#
# Env overrides (defaults come from the "core" E2E fixture, see
# scripts/e2e/lib.sh's resolve_e2e_fixture -- DATASET_ID/DATASET_VERSION are
# SceneOps' own canonical identity; SOURCE_FORMAT_VERSION is the separate,
# real nuScenes SDK version the dataroot below actually contains):
#   API_BASE_URL            (default: http://localhost:8000)
#   DATASET_ID              (default: test-e2e-core)
#   DATASET_VERSION         (default: test-v1)
#   SOURCE_FORMAT_VERSION   (default: v1.0-mini)
#   SOURCE_ROOT_URI         (default: /data/raw/nuscenes)
#   MAX_SCENES              how many nuScenes scenes to ingest (default: 10 --
#                           scenario curation's own selectability logic
#                           needs more than a handful of scenes to produce
#                           a non-empty ScenarioSet, see e2e-perception)
#   POLL_TIMEOUT            max poll attempts, 5s each (default: 60 = 5 min)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib.sh"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
resolve_e2e_fixture core
MAX_SCENES="${MAX_SCENES:-10}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"

SOURCE_FORMAT="${SOURCE_FORMAT:-nuscenes}"
SOURCE_FORMAT_VERSION="${SOURCE_FORMAT_VERSION:-v1.0-mini}"
SOURCE_ROOT_URI="${SOURCE_ROOT_URI:-/data/raw/nuscenes}"

echo "=== e2e-scene: dataset_scene_ingestion E2E ==="
echo "  API_BASE_URL=$API_BASE_URL"
echo "  DATASET_ID=$DATASET_ID  DATASET_VERSION=$DATASET_VERSION"
echo "  SOURCE_FORMAT_VERSION=$SOURCE_FORMAT_VERSION  SOURCE_ROOT_URI=$SOURCE_ROOT_URI  MAX_SCENES=$MAX_SCENES"
echo ""

# ── 1. Ensure dataset and version exist ──────────────────────────────────────

echo "--- 1. Upsert dataset ---"
upsert_dataset "$API_BASE_URL" "$DATASET_ID" "nuScenes" | jq '.dataset | {datasetId}' 2>/dev/null || true
echo ""

echo "--- 1b. Upsert dataset version (with raw_source_root_uri) ---"
upsert_dataset_version "$API_BASE_URL" "$DATASET_ID" "$DATASET_VERSION" "$SOURCE_ROOT_URI" \
  | jq '.version | {version, status, scene}' 2>/dev/null || true
echo ""

# ── 2. Create pipeline run ────────────────────────────────────────────────────

echo "--- 2. Create pipeline run ---"
PAYLOAD="$(cat <<JSON
{
  "type": "dataset_scene_ingestion",
  "dataset_id": "$DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "ingest_scenes": {
      "source_format": "$SOURCE_FORMAT",
      "source_root_uri": "$SOURCE_ROOT_URI",
      "source_format_version": "$SOURCE_FORMAT_VERSION",
      "max_source_scenes": $MAX_SCENES,
      "mode": "upsert"
    },
    "register_scene": {
      "replace_existing": true
    },
    "validate_scene": {
      "require_target_channels": ["CAM_FRONT", "LIDAR_TOP"]
    },
    "profile_scene": {
      "profile_samples": true,
      "profile_assets": true
    },
    "build_scene_index": {},
    "build_dataset_manifest": {}
  }
}
JSON
)"

CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$PAYLOAD")"
PIPELINE_RUN_ID="$(extract_pipeline_run_id "$CREATE_RESP")"
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo ""

# ── 3. Execute ────────────────────────────────────────────────────────────────

echo "--- 3. Dispatch ---"
EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$PIPELINE_RUN_ID")"
EXEC_STATUS="$(echo "$EXEC_RESP" | jq -r '.execution.status // "error"')"
echo "  execution status=$EXEC_STATUS"
if [ "$EXEC_STATUS" = "error" ]; then
  echo "$EXEC_RESP" | jq . >&2
  exit 1
fi
echo ""

# ── 4. Poll ───────────────────────────────────────────────────────────────────

echo "--- 4. Polling (up to $((POLL_TIMEOUT * 5))s) ---"
PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$PIPELINE_RUN_ID" "$POLL_TIMEOUT" 5)"
echo ""

# ── 5. Assert pipeline succeeded ─────────────────────────────────────────────

echo "--- 5. Assert pipeline ---"
FINAL_STATUS="$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.status')"
echo "  status=$FINAL_STATUS"

if [ "$FINAL_STATUS" = "failed" ]; then
  echo "  error=$(echo "$PIPELINE_JSON" | jq -r '.pipelineRun.error.message // "unknown"')"
fi

assert_pipeline_succeeded "$PIPELINE_JSON" 'dataset_scene_ingestion pipeline should succeed' "$API_BASE_URL" "$PIPELINE_RUN_ID"
echo "  OK"
echo ""

# ── 6. Assert all steps succeeded ────────────────────────────────────────────

echo "--- 6. Assert steps ---"
TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$PIPELINE_RUN_ID")"
TASK_COUNT="$(echo "$TASKS_JSON" | jq '.tasks | length')"
FAILED_TASKS="$(echo "$TASKS_JSON" | jq -r '[.tasks[] | select(.status != "succeeded")] | map("\(.pipelineTaskId)=\(.status)") | join(", ")')"

echo "$TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'

if [ -n "$FAILED_TASKS" ]; then
  echo "❌ Non-succeeded tasks: $FAILED_TASKS" >&2
  exit 1
fi
echo "  All $TASK_COUNT steps: OK"
echo ""

# ── 6b. Assert key task refs ──────────────────────────────────────────────────

echo "--- 6b. Assert task refs ---"

INGEST_URIS="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "ingest_scenes") | .result.refs.scene_manifest_uris | length // 0')"
echo "  ingest_scenes scene_manifest_uris count=$INGEST_URIS"
if [ "${INGEST_URIS:-0}" -lt 1 ]; then
  echo "❌ ingest_scenes: expected scene_manifest_uris" >&2; exit 1
fi

REG_COUNT="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "register_scene") | .result.summary.registered_scene_count // 0')"
echo "  register_scene registered_scene_count=$REG_COUNT"
if [ "${REG_COUNT:-0}" -lt 1 ]; then
  echo "❌ register_scene: expected registered_scene_count > 0" >&2; exit 1
fi

SCENE_INDEX_URI="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_scene_index") | .result.artifacts.scene_index_uri // empty')"
echo "  build_scene_index scene_index_uri=$SCENE_INDEX_URI"
if [ -z "$SCENE_INDEX_URI" ]; then
  echo "❌ build_scene_index: expected scene_index_uri" >&2; exit 1
fi

MANIFEST_URI_TASK="$(echo "$TASKS_JSON" | jq -r '.tasks[] | select(.pipelineTaskId == "build_dataset_manifest") | .result.refs.dataset_manifest_uri // empty')"
echo "  build_dataset_manifest dataset_manifest_uri=$MANIFEST_URI_TASK"
if [ -z "$MANIFEST_URI_TASK" ]; then
  echo "❌ build_dataset_manifest: expected dataset_manifest_uri" >&2; exit 1
fi
echo "  OK"
echo ""

# ── 6c. Assert validate_scene result refs ──────────────────────────────────

echo "--- 6c. Assert validate_scene result refs ---"
VALIDATE_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "validate_scene")')"

VALIDATION_RUN_ID="$(echo "$VALIDATE_TASK" | jq -r '.result.refs.validation_run_id // empty')"
VALIDATION_REPORT_URI="$(echo "$VALIDATE_TASK" | jq -r '.result.artifacts.validation_report_uri // empty')"
VALIDATION_STATUS="$(echo "$VALIDATE_TASK" | jq -r '.result.summary.validation_status // empty')"
CHECKED_COUNT="$(echo "$VALIDATE_TASK" | jq -r '.result.summary.checked_scene_count // 0')"

echo "  validation_run_id=$VALIDATION_RUN_ID"
echo "  validation_report_uri=$VALIDATION_REPORT_URI"
echo "  validation_status=$VALIDATION_STATUS"
echo "  checked_scene_count=$CHECKED_COUNT"

[ -n "$VALIDATION_RUN_ID" ] || { echo "❌ validate_scene: missing validation_run_id in result refs" >&2; exit 1; }
[ -n "$VALIDATION_REPORT_URI" ] || { echo "❌ validate_scene: missing validation_report_uri" >&2; exit 1; }
[ -n "$VALIDATION_STATUS" ] || { echo "❌ validate_scene: missing validation_status" >&2; exit 1; }
[ "${CHECKED_COUNT:-0}" -ge 1 ] || { echo "❌ validate_scene: checked_scene_count should be >= 1, got $CHECKED_COUNT" >&2; exit 1; }
echo "  OK"
echo ""

# ── 6d. Assert profile_scene result refs ───────────────────────────────────

echo "--- 6d. Assert profile_scene result refs ---"
PROFILE_TASK="$(echo "$TASKS_JSON" | jq '.tasks[] | select(.pipelineTaskId == "profile_scene")')"

PROFILE_RUN_ID="$(echo "$PROFILE_TASK" | jq -r '.result.refs.profile_run_id // empty')"
PROFILE_REPORT_URI="$(echo "$PROFILE_TASK" | jq -r '.result.artifacts.profile_report_uri // empty')"

echo "  profile_run_id=$PROFILE_RUN_ID"
echo "  profile_report_uri=$PROFILE_REPORT_URI"

[ -n "$PROFILE_RUN_ID" ] || { echo "❌ profile_scene: missing profile_run_id" >&2; exit 1; }
[ -n "$PROFILE_REPORT_URI" ] || { echo "❌ profile_scene: missing profile_report_uri" >&2; exit 1; }
echo "  OK"
echo ""

# ── 7. Assert scenes created ─────────────────────────────────────────────────

echo "--- 7. Assert scenes ---"
SCENES_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")")"
SCENE_COUNT="$(echo "$SCENES_JSON" | jq '.scenes | length')"
echo "  scene_count=$SCENE_COUNT"
echo "$SCENES_JSON" | jq -r '.scenes[] | "  \(.sceneId)  status=\(.status)"'

if [ "$SCENE_COUNT" -lt 1 ]; then
  echo "❌ Expected at least 1 scene, got 0" >&2
  exit 1
fi
echo "  OK"
echo ""

# ── 7b. Assert canonical Scene identity (not just count) ─────────────────────
# Regression for the Scene-persistence bug (SceneOps V2): a matching COUNT
# alone doesn't prove these rows are actually owned by THIS dataset/version --
# the bug that slipped past every earlier assertion here was exactly a count
# that looked right while ownership had silently been reassigned to a
# different DatasetVersion (scene_id was, before the fix, the bare external
# nuScenes scene name -- not scoped by dataset_id/dataset_version -- so a
# LATER registration under a different dataset could overwrite these rows in
# place with no error and no count change here). Every returned scene must
# report ITS OWN datasetId/datasetVersion as this run's, not merely exist.

echo "--- 7b. Assert canonical Scene identity ---"
MISMATCHED_OWNER_COUNT="$(echo "$SCENES_JSON" | jq --arg d "$DATASET_ID" --arg v "$DATASET_VERSION" \
  '[.scenes[] | select(.datasetId != $d or .datasetVersion != $v)] | length')"
if [ "${MISMATCHED_OWNER_COUNT:-0}" -ne 0 ]; then
  echo "❌ $MISMATCHED_OWNER_COUNT scene(s) returned by GET /scenes?dataset_id=$DATASET_ID do not actually report that ownership" >&2
  echo "$SCENES_JSON" | jq '.scenes[] | {sceneId, datasetId, datasetVersion}' >&2
  exit 1
fi
echo "  OK ($SCENE_COUNT/$SCENE_COUNT scenes correctly owned by $DATASET_ID:$DATASET_VERSION)"
echo ""

# ── 8. Assert dataset version ready + quality cache ────────────────────────

echo "--- 8. Assert dataset version ---"
VERSION_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")")"
VERSION_STATUS="$(echo "$VERSION_JSON" | jq -r '.version.status')"
MANIFEST_URI="$(echo "$VERSION_JSON" | jq -r '.version.scene.manifestUri // empty')"
VERSION_SCENE_COUNT="$(echo "$VERSION_JSON" | jq -r '.version.scene.sceneCount // 0')"
SAMPLE_COUNT="$(echo "$VERSION_JSON" | jq -r '.version.scene.sampleCount // 0')"

echo "  status=$VERSION_STATUS"
echo "  sceneCount=$VERSION_SCENE_COUNT  sampleCount=$SAMPLE_COUNT"
echo "  manifestUri=$MANIFEST_URI"

assert_json_not_empty "$VERSION_JSON" '.version.scene.manifestUri' 'dataset version scene.manifestUri'

echo "$VERSION_JSON" | jq '.version.scene | {
  latestValidationRunId, validationStatus, shouldBlockPipeline, validationReportUri,
  latestProfileRunId, profileReportUri
}' 2>/dev/null || true

assert_json_not_empty "$VERSION_JSON" '.version.scene.latestValidationRunId' "scene summary latestValidationRunId"
assert_json_equals "$VERSION_JSON" '.version.scene.shouldBlockPipeline' "false" "scene summary shouldBlockPipeline should be false"
assert_json_not_empty "$VERSION_JSON" '.version.scene.latestProfileRunId' "scene summary latestProfileRunId"

QUAL_VALIDATION_RUN_ID="$(echo "$VERSION_JSON" | jq -r '.version.scene.latestValidationRunId // empty')"
QUAL_PROFILE_RUN_ID="$(echo "$VERSION_JSON" | jq -r '.version.scene.latestProfileRunId // empty')"
[ "$QUAL_VALIDATION_RUN_ID" = "$VALIDATION_RUN_ID" ] || { echo "❌ scene summary latestValidationRunId ($QUAL_VALIDATION_RUN_ID) != task result ($VALIDATION_RUN_ID)" >&2; exit 1; }
[ "$QUAL_PROFILE_RUN_ID" = "$PROFILE_RUN_ID" ] || { echo "❌ scene summary latestProfileRunId ($QUAL_PROFILE_RUN_ID) != task result ($PROFILE_RUN_ID)" >&2; exit 1; }
echo "  run-id cross-check: OK"

QUALITY_JSON="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION/quality")")"
echo "$QUALITY_JSON" | jq '{readiness, counts, groundTruth, manifestUri}' 2>/dev/null || true
assert_json_equals "$QUALITY_JSON" '.readiness' "ready" "quality readiness should be ready"
assert_json_gt "$QUALITY_JSON" '.counts.sceneCount' 0 "quality counts.sceneCount should be > 0"
assert_json_equals "$QUALITY_JSON" '.groundTruth.hasGroundTruth' "true" "quality groundTruth.hasGroundTruth should be true"
assert_json_not_empty "$QUALITY_JSON" '.manifestUri' "quality manifestUri should be non-empty"
echo "  OK"
echo ""

# ── 8b. Canonical Scene-persistence invariant (SceneOps V2 Scene-persistence bug fix) ──
# The exact assertion this bug slipped past: registered_scene_count (job
# result) and DatasetVersion.scene.sceneCount (a write-once-per-pipeline-run
# cached snapshot, itself derived from a live scoped query at write time --
# see apps/worker/sceneops_worker/jobs/dataset/build_dataset_manifest.py)
# both looked correct even while the real GET /scenes count -- and the real
# canonical rows -- were 0, because a LATER, unrelated registration (this
# script's own step 10 skip-test sub-run, or any other dataset ingesting the
# same real nuScenes scene) silently reassigned ownership of the same
# globally-keyed rows out from under this dataset AFTER this snapshot was
# taken. This invariant must hold from a fresh, independent read of every
# one of these four sources, not just internal self-consistency between
# pipeline task results.

echo "--- 8b. Assert canonical Scene-persistence invariant ---"
QUALITY_SCENE_COUNT="$(echo "$QUALITY_JSON" | jq -r '.counts.sceneCount // 0')"
echo "  registered_scene_count (job result)      = $REG_COUNT"
echo "  DatasetVersion.scene.sceneCount (cache)   = $VERSION_SCENE_COUNT"
echo "  GET /scenes count (canonical rows, live)  = $SCENE_COUNT"
echo "  GET .../quality counts.sceneCount (live)  = $QUALITY_SCENE_COUNT"

if [ "$REG_COUNT" != "$VERSION_SCENE_COUNT" ] || [ "$VERSION_SCENE_COUNT" != "$SCENE_COUNT" ] || [ "$SCENE_COUNT" != "$QUALITY_SCENE_COUNT" ]; then
  echo "❌ Scene-persistence invariant violated: registered_scene_count=$REG_COUNT" >&2
  echo "   DatasetVersion.scene.sceneCount=$VERSION_SCENE_COUNT  GET /scenes count=$SCENE_COUNT" >&2
  echo "   GET .../quality counts.sceneCount=$QUALITY_SCENE_COUNT -- these must all agree." >&2
  echo "   A pipeline that reports success while canonical Scene rows are missing or" >&2
  echo "   reassigned must fail here, not silently pass." >&2
  exit 1
fi
echo "  OK — pipeline output, cached summary, and live canonical rows all agree ($SCENE_COUNT)"
echo ""

# ── 9. Assert dataset manifest artifact ──────────────────────────────────────

echo "--- 9. Assert artifacts ---"
ARTIFACTS_JSON="$(fetch_artifacts_by_owner "$API_BASE_URL" "dataset_version" "$DATASET_ID:$DATASET_VERSION")"
assert_artifact_kind_present "$ARTIFACTS_JSON" "dataset_manifest" "expected at least 1 dataset_manifest artifact"
echo "  OK"
echo ""

# ── 10. Optional-task-skip regression ──────────────────────────────────────
# Confirms profile_scene is correctly marked SKIPPED (not a failure) when no
# caller params are given, and every required task still succeeds around it
# -- isolated under its own dataset_id so this second, deliberately-partial
# registration doesn't skew the main run's aggregate /quality readiness on
# the shared "core" fixture.

echo "--- 10. Verify optional task skip (no validate/profile params) ---"
SKIP_TEST_DATASET_ID="${DATASET_ID}-skip-test"
upsert_dataset "$API_BASE_URL" "$SKIP_TEST_DATASET_ID" "nuScenes (skip-test)" >/dev/null 2>&1 || true

SKIP_PAYLOAD="$(cat <<JSON
{
  "type": "dataset_scene_ingestion",
  "dataset_id": "$SKIP_TEST_DATASET_ID",
  "dataset_version": "$DATASET_VERSION",
  "force": true,
  "params": {
    "ingest_scenes": {
      "source_format": "$SOURCE_FORMAT",
      "source_root_uri": "$SOURCE_ROOT_URI",
      "source_format_version": "$SOURCE_FORMAT_VERSION",
      "max_source_scenes": $MAX_SCENES,
      "mode": "upsert"
    },
    "register_scene": {
      "replace_existing": true
    },
    "build_scene_index": {},
    "build_dataset_manifest": {}
  }
}
JSON
)"

SKIP_CREATE_RESP="$(create_pipeline_run "$API_BASE_URL" "$SKIP_PAYLOAD")"
SKIP_RUN_ID="$(extract_pipeline_run_id "$SKIP_CREATE_RESP")"
echo "  skip pipeline_run_id=$SKIP_RUN_ID"

SKIP_EXEC_RESP="$(dispatch_pipeline_run "$API_BASE_URL" "$SKIP_RUN_ID")"
SKIP_EXEC_STATUS="$(echo "$SKIP_EXEC_RESP" | jq -r '.execution.status // "error"')"
if [ "$SKIP_EXEC_STATUS" = "error" ]; then
  echo "$SKIP_EXEC_RESP" | jq . >&2
  exit 1
fi

SKIP_PIPELINE_JSON="$(poll_pipeline_terminal "$API_BASE_URL" "$SKIP_RUN_ID" "$POLL_TIMEOUT" 5)"
assert_pipeline_succeeded "$SKIP_PIPELINE_JSON" 'skip-test pipeline should succeed' "$API_BASE_URL" "$SKIP_RUN_ID"

SKIP_TASKS_JSON="$(fetch_pipeline_tasks "$API_BASE_URL" "$SKIP_RUN_ID")"
echo "$SKIP_TASKS_JSON" | jq -r '.tasks[] | "  \(.pipelineTaskId): \(.status)"'

for task_id in profile_scene; do
  SKIP_TASK_STATUS="$(echo "$SKIP_TASKS_JSON" | jq -r --arg t "$task_id" '.tasks[] | select(.pipelineTaskId == $t) | .status // empty')"
  [ "$SKIP_TASK_STATUS" = "skipped" ] || { echo "❌ Task '$task_id' expected skipped (no params), got '$SKIP_TASK_STATUS'" >&2; exit 1; }
done
for task_id in ingest_scenes register_scene validate_scene build_scene_index build_dataset_manifest; do
  SKIP_TASK_STATUS="$(echo "$SKIP_TASKS_JSON" | jq -r --arg t "$task_id" '.tasks[] | select(.pipelineTaskId == $t) | .status // empty')"
  [ "$SKIP_TASK_STATUS" = "succeeded" ] || { echo "❌ Required task '$task_id' expected succeeded after optional skip, got '$SKIP_TASK_STATUS'" >&2; exit 1; }
done
echo "  Optional task skip: OK"
echo ""

# ── 11. Re-verify the Scene-persistence invariant AFTER a second, related ────
#     registration (${DATASET_ID}-skip-test) has run.
#
# This is the exact scenario the original bug slipped past: step 8b's own
# invariant checked out fine at the time, and it was ONLY this later,
# unrelated dataset's registration (of what were, pre-fix, colliding bare
# scene_ids) that silently reassigned the primary dataset's already-"passing"
# rows. Checking the invariant only once, before this second registration
# runs, would not have caught the bug at all -- it must be re-verified here,
# against a fresh, independent read, after.

echo "--- 11. Re-verify Scene-persistence invariant after a second, related registration ---"
SCENES_JSON_AFTER="$(curl -sS "$(api_url "$API_BASE_URL" "/scenes?dataset_id=$DATASET_ID&dataset_version=$DATASET_VERSION")")"
SCENE_COUNT_AFTER="$(echo "$SCENES_JSON_AFTER" | jq '.scenes | length')"
VERSION_JSON_AFTER="$(curl -sS "$(api_url "$API_BASE_URL" "/datasets/$DATASET_ID/versions/$DATASET_VERSION")")"
VERSION_SCENE_COUNT_AFTER="$(echo "$VERSION_JSON_AFTER" | jq -r '.version.scene.sceneCount // 0')"
echo "  GET /scenes count (after)                = $SCENE_COUNT_AFTER"
echo "  DatasetVersion.scene.sceneCount (after)   = $VERSION_SCENE_COUNT_AFTER"

if [ "$SCENE_COUNT_AFTER" != "$SCENE_COUNT" ]; then
  echo "❌ $DATASET_ID:$DATASET_VERSION's own Scene rows changed after an unrelated" >&2
  echo "   dataset's registration (${DATASET_ID}-skip-test) — was $SCENE_COUNT, now $SCENE_COUNT_AFTER." >&2
  echo "   This is exactly the Scene-persistence bug: canonical rows silently" >&2
  echo "   reassigned to a different DatasetVersion." >&2
  exit 1
fi
MISMATCHED_OWNER_COUNT_AFTER="$(echo "$SCENES_JSON_AFTER" | jq --arg d "$DATASET_ID" --arg v "$DATASET_VERSION" \
  '[.scenes[] | select(.datasetId != $d or .datasetVersion != $v)] | length')"
[ "${MISMATCHED_OWNER_COUNT_AFTER:-0}" -eq 0 ] || {
  echo "❌ $MISMATCHED_OWNER_COUNT_AFTER scene(s) no longer report ownership by $DATASET_ID:$DATASET_VERSION after the second registration" >&2
  exit 1
}
echo "  OK — $DATASET_ID:$DATASET_VERSION's Scene rows are unchanged and still correctly owned"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────

echo "=== PASSED ==="
echo "  pipeline_run_id=$PIPELINE_RUN_ID"
echo "  scenes=$SCENE_COUNT  samples=$SAMPLE_COUNT"
echo "  manifest_uri=$MANIFEST_URI"
echo "  validation_run_id=$VALIDATION_RUN_ID  profile_run_id=$PROFILE_RUN_ID"
