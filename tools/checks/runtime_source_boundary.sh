#!/usr/bin/env bash
# Runtime source boundary (`make check-runtime-boundary`):
#
#   raw source                         -> acquisition / reference preparation only
#   locked reference MCAP + labels     -> normal SceneOps runtime
#
# 1. Static: in the rendered compose configuration of every profile, only the
#    acquisition and reference-preparation services mount the raw dataset (or
#    any directory that contains it).
# 2. Dynamic: each normal runtime service is started with its own mounts and
#    must see neither /data/raw nor /input/nuscenes (nor the reference cache
#    through a /data mount), while the paths it genuinely needs stay readable.
#    A service whose image is not built is probed through its mount list in
#    the worker image, and reported as such.
#
# Needs Docker Compose, jq and the worker image. Starts no platform service.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
ENV_FILE="${ENV_FILE:-.env.local}"
COMPOSE=(docker compose --env-file "$ENV_FILE"
  --profile worker --profile debug --profile inference --profile lerobot
  --profile acquisition --profile tools --profile streaming --profile recovery)
PROBE_IMAGE="sceneops-platform/worker:local"

# The only services allowed to see the raw dataset.
SOURCE_SERVICES="dataset-acquisition reference-data"
# Raw-source paths that must not exist in any other service.
FORBIDDEN="/data/raw /data/reference /input/nuscenes"

failures=0
fail() { echo "  ❌  $*" >&2; failures=$((failures + 1)); }
ok() { echo "  ✅  $*"; }

CONFIG="$("${COMPOSE[@]}" config --format json)"

echo "=== [1/2] static: which services mount the raw dataset ==="
RAW_DIR="$REPO_ROOT/data/raw"
# A bind mount exposes the raw dataset when its source is the raw directory, a
# path under it, or a parent of it (e.g. ./data).
EXPOSED="$(jq -r --arg raw "$RAW_DIR" '
  .services | to_entries[] | .key as $svc
  | (.value.volumes // [])[] | select(.type == "bind")
  | select(.source as $s | $s == $raw or ($s | startswith($raw + "/")) or ($raw | startswith($s + "/")))
  | "\($svc) \(.source) -> \(.target)"' <<<"$CONFIG")"
while IFS= read -r line; do
  [ -n "$line" ] || continue
  svc="${line%% *}"
  case " $SOURCE_SERVICES " in
    *" $svc "*) ok "source-preparation service may mount raw: $line" ;;
    *) fail "runtime service mounts the raw dataset: $line" ;;
  esac
done <<<"$EXPOSED"
for svc in $SOURCE_SERVICES; do
  grep -q "^$svc " <<<"$EXPOSED" || fail "$svc no longer mounts the raw dataset it exists to read"
done

echo ""
echo "=== [2/2] dynamic: what each runtime service can see ==="

# probe <service> <required path>...
probe() {
  local svc="$1"
  shift
  local script="" p out image mode
  for p in $FORBIDDEN; do script+="[ -e $p ] && echo visible:$p; "; done
  for p in "$@"; do script+="[ -r $p ] || echo missing:$p; "; done
  script+="echo done"
  image="$(jq -r --arg s "$svc" '.services[$s].image // empty' <<<"$CONFIG")"
  if docker image inspect "$image" >/dev/null 2>&1; then
    mode="container"
    out="$("${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh "$svc" -c "$script" </dev/null 2>&1)" || true
  else
    mode="mounts only; image $image not built"
    local args=()
    while IFS= read -r v; do args+=(-v "$v"); done < <(jq -r --arg s "$svc" '
      (.services[$s].volumes // [])[] | select(.type == "bind")
      | "\(.source):\(.target)\(if .read_only then ":ro" else "" end)"' <<<"$CONFIG")
    out="$(docker run --rm --entrypoint sh "${args[@]}" "$PROBE_IMAGE" -c "$script" </dev/null 2>&1)" || true
  fi
  if [ "$(tail -1 <<<"$out")" != done ] || [ "$(wc -l <<<"$out" | tr -d ' ')" != 1 ]; then
    fail "$svc ($mode): $(tr '\n' ' ' <<<"$out")"
  else
    ok "$svc ($mode): no raw source visible; required paths readable: ${*:-none}"
  fi
}

probe api /data/artifacts
probe worker-pipeline /data/artifacts /data/inputs
probe worker-jobs /data/artifacts /data/inputs
probe worker-cli /data/artifacts /data/inputs /data/runs
probe inference-server-local /data/artifacts
probe streaming-bridge /workspace/channels
probe capture /recordings /workspace/channels
probe lerobot-integration /data/artifacts /data/runs
probe dataset-replay /reference /config/reference
probe recording-publisher /recordings /reference
probe reference-conformance /reference
probe reference-verify /reference
probe reference-labels /reference /config/reference /inputs
probe publication-recovery /recordings
probe registration-recovery /workspace/ops
probe minio-init /scripts/minio_init.sh

# Positive control: the probe can see the raw dataset where it is allowed.
if [ -d "$RAW_DIR/nuscenes" ]; then
  seen="$("${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh reference-data -c '[ -e /input/nuscenes ] && echo visible' </dev/null 2>&1 || true)"
  [ "$seen" = visible ] && ok "control: reference-data sees /input/nuscenes" \
    || fail "control: reference-data does not see /input/nuscenes ($seen)"
fi

echo ""
if [ "$failures" -ne 0 ]; then
  echo "❌ runtime source boundary violated ($failures failure(s))" >&2
  exit 1
fi
echo "✅ runtime source boundary holds"
