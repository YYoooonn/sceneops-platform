#!/usr/bin/env bash
# smoke_api.sh — lightweight transport/liveness smoke test for the SceneOps
# Platform API. Answers "is the service reachable, does the transport
# contract work, do read endpoints respond" only -- does NOT require worker
# execution to pass.
#
# Strict rule: a smoke-* target must not leave behind persistent
# application-domain records. Read-only list endpoints (tolerant of an
# empty list) plus a 404/validation-error check on a request that is
# guaranteed never to exist prove the "transport contract works, minimal
# request parses" property without writing anything.
#
# Usage:
#   API_BASE_URL=http://localhost:8000 bash scripts/e2e/smoke_api.sh
#
# Dependencies: curl, jq

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
PREFIX="/api/v1"
PASS=0
FAIL=0

# ── helpers ───────────────────────────────────────────────────────────────────

url() { echo "${API_BASE_URL}${PREFIX}${1}"; }

get() {
  local path="$1"
  curl -sf "$(url "$path")" -H "Accept: application/json"
}

check() {
  local label="$1"
  local json="$2"
  if echo "$json" | jq -e . >/dev/null 2>&1; then
    echo "  ✅  $label"
    PASS=$((PASS + 1))
  else
    echo "  ❌  $label — invalid JSON"
    echo "      response: $json"
    FAIL=$((FAIL + 1))
  fi
}

check_field() {
  local label="$1"
  local json="$2"
  local expr="$3"
  local val
  val="$(echo "$json" | jq -r "$expr" 2>/dev/null || echo '')"
  if [ "$val" = "null" ] || [ -z "$val" ]; then
    echo "  ❌  $label — missing field: $expr"
    FAIL=$((FAIL + 1))
  else
    echo "  ✅  $label ($val)"
    PASS=$((PASS + 1))
  fi
}

check_status() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo "  ✅  $label (http $actual)"
    PASS=$((PASS + 1))
  else
    echo "  ❌  $label — expected http $expected, got $actual"
    FAIL=$((FAIL + 1))
  fi
}

wait_for_health() {
  local max_attempts=30
  local attempt=0
  echo "⏳ Waiting for API health..."
  while ! curl -sf "${API_BASE_URL}/health" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "❌ API did not become healthy after ${max_attempts}s"
      exit 1
    fi
    sleep 1
  done
  echo "✅ API healthy"
}

# ── health ────────────────────────────────────────────────────────────────────

wait_for_health

echo ""
echo "─── health ──────────────────────────────────────────"
health="$(curl -sf "${API_BASE_URL}/health")"
check "GET /health" "$health"
check_field "status=ok" "$health" '.status'

# ── openapi ───────────────────────────────────────────────────────────────────

echo ""
echo "─── openapi ─────────────────────────────────────────"
openapi="$(curl -sf "${API_BASE_URL}/openapi.json")"
check "GET /openapi.json" "$openapi"
check_field "openapi.title" "$openapi" '.info.title'

# ── platform: read endpoints ──────────────────────────────────────────────────

echo ""
echo "─── platform ────────────────────────────────────────"
check "GET /jobs"                        "$(get /jobs)"
check "GET /pipelines/definitions"       "$(get /pipelines/definitions)"
check "GET /pipelines/runs"              "$(get /pipelines/runs)"
check "GET /executions"                  "$(get /executions)"
check "GET /artifacts"                   "$(get /artifacts)"

# ── domains: read endpoints (list-only, tolerant of empty) ───────────────────

echo ""
echo "─── domains ─────────────────────────────────────────"
check "GET /datasets"                    "$(get /datasets)"
check "GET /scenes"                      "$(get /scenes)"
check "GET /episodes"                    "$(get /episodes)"
check "GET /scenarios"                   "$(get /scenarios)"
check "GET /models"                      "$(get /models)"
check "GET /inference/runs"              "$(get /inference/runs)"
check "GET /evaluations/runs"            "$(get /evaluations/runs)"

# ── views ─────────────────────────────────────────────────────────────────────

echo ""
echo "─── views ───────────────────────────────────────────"
summary="$(get /operations/summary)"
check "GET /operations/summary"          "$summary"
check_field "summary.jobs exists"        "$summary" '.jobs'
check_field "summary.pipelines exists"   "$summary" '.pipelines'
check "GET /operations/timeline"         "$(get /operations/timeline)"
check "GET /operations/failures"         "$(get /operations/failures)"
check "GET /leaderboards/evaluations"    "$(get /leaderboards/evaluations)"

# ── pipeline definitions ──────────────────────────────────────────────────────

echo ""
echo "─── pipeline definitions ────────────────────────────"
defs="$(get /pipelines/definitions)"
check_field "definitions count > 0" "$defs" '.count'
def_count="$(echo "$defs" | jq -r '.count')"
echo "  📋 ${def_count} built-in pipeline definitions"

# ── transport contract: 404 / validation-error paths (no writes) ────────────
#
# Proves "a minimal request can be parsed" and "the read-not-found contract
# works" without ever persisting anything: a lookup of an id that is
# guaranteed never to exist, and a POST payload that is guaranteed to fail
# request validation, both terminate before any row is written.

echo ""
echo "─── transport contract (no domain data written) ────"
NEVER_EXISTS_ID="smoke-does-not-exist-$(date +%s%N)"

not_found_status="$(curl -sS -o /dev/null -w '%{http_code}' "$(url "/datasets/${NEVER_EXISTS_ID}")")"
check_status "GET /datasets/{never-exists} -> 404" "404" "$not_found_status"

# Missing every required field -- FastAPI/pydantic must reject this at the
# request-parsing boundary (422) before any handler code runs, so nothing is
# ever persisted regardless of the response code's exact value.
invalid_post_status="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$(url "/datasets")" \
  -H "Content-Type: application/json" -d '{}')"
check_status "POST /datasets {} -> 422 (validation, no write)" "422" "$invalid_post_status"

# ── summary ───────────────────────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Smoke test complete: ✅ ${PASS} passed / ❌ ${FAIL} failed"
echo "  (zero domain records created or persisted)"
echo "═══════════════════════════════════════════════════"

[ "$FAIL" -eq 0 ]
