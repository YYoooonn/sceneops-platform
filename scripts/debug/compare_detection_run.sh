#!/usr/bin/env bash
# compare_detection_run.sh
#
# Print dataset quality summary followed by a scene-level
# selection/evaluation comparison table for a detection_evaluation pipeline run.
#
# Usage:
#   bash scripts/debug/compare_detection_run.sh --pipeline-run-id <id>
#   bash scripts/debug/compare_detection_run.sh \
#       --inference-run-id <id> --evaluation-run-id <id>
#
# Env overrides:
#   API_BASE_URL      (default: http://localhost:8000)
#                     Accepts either the host root (http://localhost:8000) or
#                     the versioned base (http://localhost:8000/api/v1); both work.
#   API_V1_BASE       explicit override for the /api/v1 base; derived from
#                     API_BASE_URL when not set (avoids double-prefixing).
#   DATASET_ID        fallback when dataset_id cannot be resolved from run records
#   DATASET_VERSION   fallback when dataset_version cannot be resolved from run records
#   STRICT_QUALITY    set to 1 to fail when dataset quality cannot be fetched

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_PREFIX="${API_PREFIX:-/api/v1}"
PIPELINE_RUN_ID="${PIPELINE_RUN_ID:?PIPELINE_RUN_ID is required}"
DATASET_ID="${DATASET_ID:-nuscenes}"
DATASET_VERSION="${DATASET_VERSION:-v1.0-mini}"
STRICT_QUALITY="${STRICT_QUALITY:-0}"
INFERENCE_RUN_ID="${INFERENCE_RUN_ID:-}"
EVALUATION_RUN_ID="${EVALUATION_RUN_ID:-}"

API_V1_BASE="${API_BASE_URL}${API_PREFIX}"

if [[ -n "$PIPELINE_RUN_ID" ]]; then
  PIPELINE_JSON="$(curl -sS "${API_V1_BASE}/pipelines/runs/${PIPELINE_RUN_ID}")"
  INFERENCE_RUN_ID="$(echo "$PIPELINE_JSON" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d['pipelineRun']['result']['outputs'].get('inference_run_id',''))")"
  EVALUATION_RUN_ID="$(echo "$PIPELINE_JSON" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d['pipelineRun']['result']['outputs'].get('evaluation_run_id',''))")"

  # Also capture dataset_id / dataset_version from the pipeline run record
  if [[ -z "$DATASET_ID" ]]; then
    DATASET_ID="$(echo "$PIPELINE_JSON" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); pr=d.get('pipelineRun',{}); print(pr.get('datasetId') or pr.get('dataset_id') or '')" 2>/dev/null || true)"
  fi
  if [[ -z "$DATASET_VERSION" ]]; then
    DATASET_VERSION="$(echo "$PIPELINE_JSON" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); pr=d.get('pipelineRun',{}); print(pr.get('datasetVersion') or pr.get('dataset_version') or '')" 2>/dev/null || true)"
  fi
fi

if [[ -z "$INFERENCE_RUN_ID" || -z "$EVALUATION_RUN_ID" ]]; then
  echo "Error: provide --pipeline-run-id OR both --inference-run-id and --evaluation-run-id" >&2
  exit 1
fi

# ── fetch run records ──────────────────────────────────────────────────────────

INFER_JSON="$(curl -sS "${API_V1_BASE}/inference/runs/${INFERENCE_RUN_ID}")"
EVAL_JSON="$(curl -sS "${API_V1_BASE}/evaluations/runs/${EVALUATION_RUN_ID}")"

# ── resolve dataset_id / dataset_version from run records ─────────────────────
# Prefer inference run, then evaluation run, then DATASET_ID/DATASET_VERSION env.

if [[ -z "$DATASET_ID" ]]; then
  DATASET_ID="$(echo "$INFER_JSON" | jq -r '.run.datasetId // .run.dataset_id // empty' 2>/dev/null || true)"
fi
if [[ -z "$DATASET_VERSION" ]]; then
  DATASET_VERSION="$(echo "$INFER_JSON" | jq -r '.run.datasetVersion // .run.dataset_version // empty' 2>/dev/null || true)"
fi
if [[ -z "$DATASET_ID" ]]; then
  DATASET_ID="$(echo "$EVAL_JSON" | jq -r '.run.datasetId // .run.dataset_id // empty' 2>/dev/null || true)"
fi
if [[ -z "$DATASET_VERSION" ]]; then
  DATASET_VERSION="$(echo "$EVAL_JSON" | jq -r '.run.datasetVersion // .run.dataset_version // empty' 2>/dev/null || true)"
fi

# ── dataset quality summary ────────────────────────────────────────────────────
# Best-effort: fetches compact scene-aggregate summary from the quality endpoint.
# Failures print a warning and continue unless STRICT_QUALITY=1.

_jq_safe() {
  # Usage: _jq_safe "$JSON" '<jq_expr>' [default]
  local result
  result="$(echo "$1" | jq -r "${2}" 2>/dev/null)" || result="${3:-n/a}"
  [[ -z "$result" || "$result" == "null" ]] && result="${3:-n/a}"
  echo "$result"
}

echo ""
echo "=== Dataset Quality ==="

if [[ -n "$DATASET_ID" && -n "$DATASET_VERSION" ]]; then
  QUALITY_URL="${API_V1_BASE}/datasets/${DATASET_ID}/versions/${DATASET_VERSION}/quality"
  QUALITY_JSON="$(curl -sf --max-time 10 "$QUALITY_URL" 2>/dev/null || echo "")"

  if [[ -n "$QUALITY_JSON" ]] && echo "$QUALITY_JSON" | jq -e '.readiness' > /dev/null 2>&1; then
    READINESS="$(_jq_safe "$QUALITY_JSON" '.readiness' 'unknown')"
    SCENE_COUNT="$(_jq_safe "$QUALITY_JSON" '.counts.sceneCount // .counts.scene_count' '0')"
    SAMPLE_COUNT="$(_jq_safe "$QUALITY_JSON" '.counts.sampleCount // .counts.sample_count' '0')"
    FRAME_COUNT="$(_jq_safe "$QUALITY_JSON" '.counts.frameCount // .counts.frame_count' '0')"
    ANNOT_COUNT="$(_jq_safe "$QUALITY_JSON" '.counts.annotationCount // .counts.annotation_count' '0')"
    GT_SCENE_COUNT="$(_jq_safe "$QUALITY_JSON" '.counts.groundTruthSceneCount // .counts.ground_truth_scene_count' '0')"
    SEL_COUNT="$(_jq_safe "$QUALITY_JSON" '.counts.selectableSceneCount // .counts.selectable_scene_count' '0')"

    READY_SCENES="$(_jq_safe "$QUALITY_JSON" '.sceneQuality.readySceneCount // .scene_quality.ready_scene_count' '0')"
    WARN_SCENES="$(_jq_safe "$QUALITY_JSON" '.sceneQuality.warningSceneCount // .scene_quality.warning_scene_count' '0')"
    BLKD_SCENES="$(_jq_safe "$QUALITY_JSON" '.sceneQuality.blockedSceneCount // .scene_quality.blocked_scene_count' '0')"
    UNKN_SCENES="$(_jq_safe "$QUALITY_JSON" '.sceneQuality.unknownSceneCount // .scene_quality.unknown_scene_count' '0')"
    NON_SEL="$(_jq_safe "$QUALITY_JSON" '.sceneQuality.nonSelectableForDetectionCount // .scene_quality.non_selectable_for_detection_count' '0')"
    OBS_CHANNELS="$(echo "$QUALITY_JSON" | jq -r '(.sceneQuality.observedChannels // .scene_quality.observed_channels // []) | join(", ")' 2>/dev/null || echo "n/a")"
    EXCL_REASONS="$(echo "$QUALITY_JSON" | jq -rc '.sceneQuality.exclusionReasonCounts // .scene_quality.exclusion_reason_counts // {}' 2>/dev/null || echo '{}')"
    GT_COVERAGE="$(_jq_safe "$QUALITY_JSON" '.groundTruth.groundTruthCoverageRatio // .ground_truth.ground_truth_coverage_ratio' '0')"
    ANNOTATED_SCENES="$(_jq_safe "$QUALITY_JSON" '.groundTruth.annotatedSceneCount // .ground_truth.annotated_scene_count' '0')"

    echo "  dataset                       : ${DATASET_ID} / ${DATASET_VERSION}"
    echo "  readiness                     : ${READINESS}"
    echo "  scene_count                   : ${SCENE_COUNT}"
    echo "  sample_count                  : ${SAMPLE_COUNT}"
    echo "  frame_count                   : ${FRAME_COUNT}"
    echo "  annotation_count              : ${ANNOT_COUNT}"
    echo "  ready/warning/blocked/unknown : ${READY_SCENES} / ${WARN_SCENES} / ${BLKD_SCENES} / ${UNKN_SCENES}"
    echo "  selectable_for_detection      : ${SEL_COUNT}"
    echo "  non_selectable_for_detection  : ${NON_SEL}"
    echo "  ground_truth_scenes           : ${GT_SCENE_COUNT}"
    echo "  annotated_scenes              : ${ANNOTATED_SCENES}"
    echo "  gt_coverage_ratio             : ${GT_COVERAGE}"
    echo "  observed_channels             : ${OBS_CHANNELS}"
    echo "  exclusion_reasons             : ${EXCL_REASONS}"
  else
    echo "  unavailable: quality endpoint returned non-JSON or error"
    echo "  url: ${QUALITY_URL}"
    if [[ "$STRICT_QUALITY" = "1" ]]; then
      echo "STRICT_QUALITY=1: failing on missing dataset quality" >&2
      exit 1
    fi
  fi
else
  echo "  unavailable: dataset_id/version not found in run records"
  echo "  hint: set DATASET_ID and DATASET_VERSION env vars as fallback"
  if [[ "$STRICT_QUALITY" = "1" ]]; then
    echo "STRICT_QUALITY=1: failing on unresolved dataset_id/version" >&2
    exit 1
  fi
fi

# ── extract scene lists via Python ────────────────────────────────────────────

python3 - "$INFERENCE_RUN_ID" "$EVALUATION_RUN_ID" <<'PYEOF'
import sys
import json

inference_run_id = sys.argv[1]
evaluation_run_id = sys.argv[2]

# Read pre-fetched JSON from stdin
infer_raw = open("/dev/stdin").read() if False else None  # handled below
PYEOF
# Pass JSON blobs and pipeline run ID to Python via environment variables
export INFER_JSON EVAL_JSON PIPELINE_RUN_ID

python3 - "$INFERENCE_RUN_ID" "$EVALUATION_RUN_ID" <<'PYEOF'
import sys
import json
import os

inference_run_id = sys.argv[1]
evaluation_run_id = sys.argv[2]
pipeline_run_id = os.environ.get("PIPELINE_RUN_ID", "")

infer_data = json.loads(os.environ["INFER_JSON"])
eval_data  = json.loads(os.environ["EVAL_JSON"])

infer_run = infer_data.get("run", {})
eval_run  = eval_data.get("run", {})

infer_meta   = infer_run.get("metadata", {}) or {}
eval_meta    = eval_run.get("metadata", {}) or {}
eval_summary = eval_run.get("summary", {}) or {}

scene_sel = infer_meta.get("sceneSelection") or infer_meta.get("scene_selection") or {}

selected_ids  = set(scene_sel.get("selected_scene_ids") or scene_sel.get("selectedSceneIds") or [])
skipped_sel   = scene_sel.get("skipped_scenes") or scene_sel.get("skippedScenes") or []

evaluated_ids = set(eval_summary.get("evaluated_scene_ids") or eval_summary.get("evaluatedSceneIds") or [])
skipped_eval  = set(eval_summary.get("skipped_scene_ids") or eval_summary.get("skippedSceneIds") or [])

# Build per-scene skip reason index from selection skips
sel_skip_reason: dict[str, str] = {}
for entry in skipped_sel:
    sid = entry.get("scene_id") or entry.get("sceneId") or ""
    reason = entry.get("reason") or ""
    if sid:
        sel_skip_reason[sid] = reason

# Build per-scene sample count from selection skips
sel_skip_samples: dict[str, int] = {}
for entry in skipped_sel:
    sid = entry.get("scene_id") or entry.get("sceneId") or ""
    sc  = entry.get("sample_count") or entry.get("sampleCount") or 0
    if sid:
        sel_skip_samples[sid] = sc

all_scene_ids = (
    selected_ids
    | {e.get("scene_id") or e.get("sceneId") or "" for e in skipped_sel}
)
all_scene_ids.discard("")

# ── ScenarioSet lineage ───────────────────────────────────────────────────────

# Prefer inference run as primary source; fall back to evaluation run.
scenario_set_id          = infer_meta.get("scenario_set_id")          or eval_meta.get("scenario_set_id")
scenario_set_uri         = infer_meta.get("scenario_set_uri")         or eval_meta.get("scenario_set_uri")
scenario_candidate_count = infer_meta.get("scenario_candidate_count") or eval_meta.get("scenario_candidate_count")
scenario_selected_count  = infer_meta.get("scenario_selected_count")  or eval_meta.get("scenario_selected_count")
scenario_rejected_count  = infer_meta.get("scenario_rejected_count")  or eval_meta.get("scenario_rejected_count")

not_in_scenario_set_count = sum(
    1 for item in skipped_sel
    if item.get("reason") == "not_in_scenario_set"
)

infer_ss_id = infer_meta.get("scenario_set_id")
eval_ss_id  = eval_meta.get("scenario_set_id")
lineage_ok = True
lineage_consistency = "-"
if infer_ss_id and eval_ss_id:
    if infer_ss_id == eval_ss_id:
        lineage_consistency = "ok"
    else:
        lineage_consistency = f"MISMATCH (inference={infer_ss_id!r}, evaluation={eval_ss_id!r})"
        lineage_ok = False
elif infer_ss_id or eval_ss_id:
    lineage_consistency = "partial (only one side has scenario_set_id)"

print("")
print("=== ScenarioSet Lineage ===")
if scenario_set_id:
    selected_scene_count  = scene_sel.get("selected_scene_count") or scene_sel.get("selectedSceneCount") or len(selected_ids)
    evaluated_scene_count = eval_summary.get("evaluated_scene_count") or eval_summary.get("evaluatedSceneCount") or len(evaluated_ids)
    cand  = scenario_candidate_count if scenario_candidate_count is not None else "-"
    sel   = scenario_selected_count  if scenario_selected_count  is not None else "-"
    rej   = scenario_rejected_count  if scenario_rejected_count  is not None else "-"
    print(f"  scenario_set_id          : {scenario_set_id}")
    print(f"  scenario_set_uri         : {scenario_set_uri or '-'}")
    print(f"  scenario_candidate_count : {cand}")
    print(f"  scenario_selected_count  : {sel}")
    print(f"  scenario_rejected_count  : {rej}")
    print(f"  not_in_scenario_set      : {not_in_scenario_set_count}")
    print(f"  lineage_consistency      : {lineage_consistency}")
    print(f"  flow                     : {sel} scenario candidates → {selected_scene_count} selected scenes → {evaluated_scene_count} evaluated scenes")
else:
    print("  scenario_set_id          : -")
    print("  mode                     : dataset-wide detection evaluation")
print("")

# ── Detection Run Comparison ──────────────────────────────────────────────────

print("=== Detection Run Comparison ===")
print(f"  inference_run_id  : {inference_run_id}")
print(f"  evaluation_run_id : {evaluation_run_id}")
print("")

# Summary counts
print("--- Summary ---")
print(f"  selected_scene_count  : {scene_sel.get('selected_scene_count') or scene_sel.get('selectedSceneCount') or len(selected_ids)}")
print(f"  selected_sample_count : {scene_sel.get('selected_sample_count') or scene_sel.get('selectedSampleCount') or '-'}")
print(f"  skipped_scene_count   : {scene_sel.get('skipped_scene_count') or scene_sel.get('skippedSceneCount') or len(skipped_sel)}")
print(f"  evaluated_scene_count : {eval_summary.get('evaluated_scene_count') or eval_summary.get('evaluatedSceneCount') or len(evaluated_ids)}")
print(f"  eval_skipped_count    : {eval_summary.get('skipped_scene_count') or eval_summary.get('skippedSceneCount') or len(skipped_eval)}")
print(f"  ground_truth_count    : {eval_summary.get('ground_truth_count') or eval_summary.get('groundTruthCount') or '-'}")
print(f"  prediction_count      : {eval_summary.get('prediction_count') or eval_summary.get('predictionCount') or '-'}")
print(f"  evaluable_pred_count  : {eval_summary.get('evaluable_prediction_count') or eval_summary.get('evaluablePredictionCount') or '-'}")
print(f"  primary_metric        : {eval_summary.get('primary_metric_name') or eval_summary.get('primaryMetricName') or '-'} = {eval_summary.get('primary_metric_value') or eval_summary.get('primaryMetricValue') or '-'}")
print("")

# Per-scene table
print("--- Scene table ---")
col_w = 32
header = (
    f"{'scene_id':<{col_w}}  {'selected':<10}  {'evaluated':<10}  "
    f"{'skip_reason':<30}  {'samples':>7}"
)
print(header)
print("-" * len(header))

for scene_id in sorted(all_scene_ids):
    is_selected  = "yes" if scene_id in selected_ids else "no"
    is_evaluated = "yes" if scene_id in evaluated_ids else "no"

    if scene_id in sel_skip_reason:
        skip_reason = sel_skip_reason[scene_id]
    elif scene_id in skipped_eval:
        skip_reason = "eval_skipped"
    else:
        skip_reason = "-"

    samples = sel_skip_samples.get(scene_id, "-")
    if scene_id in selected_ids:
        # selected scenes don't have per-scene sample count in the summary
        samples = "-"

    print(
        f"{scene_id:<{col_w}}  {is_selected:<10}  {is_evaluated:<10}  "
        f"{skip_reason:<30}  {str(samples):>7}"
    )

if not all_scene_ids:
    print("  (no scene data found in run metadata — check that the run completed successfully)")
print("")

# Fail non-zero on lineage mismatch so the caller can detect it.
if not lineage_ok:
    print("ERROR: ScenarioSet lineage mismatch — comparison results are not trustworthy.", file=sys.stderr)
    print(f"  inference  scenario_set_id : {infer_ss_id}", file=sys.stderr)
    print(f"  evaluation scenario_set_id : {eval_ss_id}", file=sys.stderr)
    if pipeline_run_id:
        print(f"  detection pipeline_run_id  : {pipeline_run_id}", file=sys.stderr)
    sys.exit(1)
PYEOF
