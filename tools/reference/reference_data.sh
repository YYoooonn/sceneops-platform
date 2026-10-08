#!/usr/bin/env bash
# reference_data.sh — prepare or verify a reference corpus (config/reference/).
#
#   bootstrap   fingerprint the source, materialize missing batch MCAPs into the
#               local cache, verify every fixture of the scope against
#               corpus.lock.json, and check each recording's L1 conformance.
#               With UPDATE_LOCK=1 the lock is (re)written from what the current
#               source and tool produce; that is the only way it changes.
#   verify      the same checks, read-only: nothing is converted or written.
#
# It runs two one-shot containers and nothing else: `reference-data` (the
# dataset-acquisition image; no network, no SceneOps package) and
# `reference-conformance` (the publisher's database-free `check`). Both run with
# --no-deps. PostgreSQL, MinIO, Redis and Kafka are never started or touched.
#
# Variables: REFERENCE_SCOPE (default smoke-1), REFERENCE_CORPUS (default
# nuscenes-mini-v1), UPDATE_LOCK (bootstrap only), REFERENCE_DATA_ROOT (cache
# root, default ./data/reference), ACQUISITION_NUSCENES_ROOT, ENV_FILE.
#
# Prerequisites: the dataset-acquisition image (`make acquisition-image`), the
# worker image (`make compose-build`), data/raw/nuscenes with v1.0-mini and
# can_bus.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

MODE="${1:-}"
case "$MODE" in bootstrap | verify) ;; *)
  echo "usage: reference_data.sh bootstrap|verify" >&2
  exit 2
  ;;
esac

ENV_FILE="${ENV_FILE:-.env.local}"
CORPUS="${REFERENCE_CORPUS:-nuscenes-mini-v1}"
SCOPE="${REFERENCE_SCOPE:-smoke-1}"
UPDATE_LOCK="${UPDATE_LOCK:-}"
export REFERENCE_DATA_ROOT="${REFERENCE_DATA_ROOT:-$REPO_ROOT/data/reference}"
if [ -n "$UPDATE_LOCK" ] && [ "$MODE" != bootstrap ]; then
  echo "❌ UPDATE_LOCK applies to bootstrap only" >&2
  exit 2
fi

log() { echo "$@" >&2; }
compose() { docker compose --env-file "$ENV_FILE" --profile acquisition "$@"; }

# Created on the host so the container's user (the caller's uid) owns it.
mkdir -p "$REFERENCE_DATA_ROOT/$CORPUS/recordings"

tool() { # <command> [extra args...]; prints the tool's JSON lines
  local command="$1"
  shift
  compose run --rm -T --no-deps --user "$(id -u):$(id -g)" -e HOME=/tmp \
    reference-data reference "$command" \
    --corpus "/config/reference/$CORPUS" --dataroot /input/nuscenes \
    --cache-root /reference --scope "$SCOPE" "$@"
}

log "=== reference corpus $CORPUS  scope=$SCOPE  mode=$MODE${UPDATE_LOCK:+  (--update-lock)} ==="
status=0
if [ "$MODE" = bootstrap ]; then
  RESULT="$(tool prepare ${UPDATE_LOCK:+--update-lock})" || status=$?
  printf '%s\n' "$RESULT" | jq -c --arg at "$(date -u +%FT%TZ)" '. + {at: $at}' \
    >>"$REFERENCE_DATA_ROOT/$CORPUS/prepare-log.jsonl" 2>/dev/null || true
else
  RESULT="$(tool verify)" || status=$?
fi

printf '%s\n' "$RESULT" | jq -r '
  (.recording // {}) as $r |
  if .status == "failed" then "  ❌  \(.fixture_id)"
  else "  ✅  \(.fixture_id)  \(.status)  \(($r.size_bytes // 0) / 1048576 | floor) MiB  \($r.message_count // 0) messages" +
       (if (.conversion_seconds // 0) > 0 then "  converted in \(.conversion_seconds) s" else "" end) end,
  (.problems[]? | "        \(.)")' >&2
[ "$status" -eq 0 ] || {
  log "❌ the reference corpus does not agree with $CORPUS/corpus.lock.json (nothing was rewritten)"
  exit 1
}

log "--- L1 conformance of the cached recordings ---"
for path in $(printf '%s\n' "$RESULT" | jq -r '.path'); do
  compose run --rm -T --no-deps reference-conformance check --mcap-path "$path" \
    | jq -e '.conforms' >/dev/null \
    || {
      log "❌ $path is not L1-conformant"
      exit 1
    }
  log "  ✅  $(basename "$path")"
done

printf '%s\n' "$RESULT" | jq -sc --arg corpus "$CORPUS" --arg scope "$SCOPE" '{
  corpus: $corpus, scope: $scope, fixtures: length,
  recording_bytes: ([.[].recording.size_bytes] | add),
  messages: ([.[].recording.message_count] | add)}'
log "=== reference corpus $CORPUS ($SCOPE) ready ==="
